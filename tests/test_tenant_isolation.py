"""Two users on one process must never see each other's memory, audio or language.

web/run.py normally loads the real models on import. Here VoiceMem is replaced by a stub, so
this tests the isolation wiring (space scoping, per-user caches, context propagation), not the
models. A two-user run against the real runtime is still part of the launch checklist.
"""

import asyncio
import importlib.util
import sys
import tempfile
import threading
import types
from pathlib import Path

import pytest

from voicemem import lang
from voicemem.utils.common import tenant

A, B = "uaaaa1111", "ubbbb2222"
WEB = Path(__file__).resolve().parents[1] / "web"


class FakeVM:
    made: list[str] = []

    def __init__(self, cfg):
        self.cfg = cfg
        self._o = types.SimpleNamespace(_user_id="u")

    @classmethod
    def from_config(cls, cfg):
        cls.made.append(cfg["space"])
        return cls(cfg)

    def warmup(self, verbose=False):
        pass


@pytest.fixture(scope="module")
def run():
    import voicemem

    pytest.importorskip("openai")
    pytest.importorskip("soundfile")
    root = tempfile.mkdtemp()
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("VOICEMEM_MEMORYSPACE_ROOT", root)
        mp.setenv("OPENAI_API_KEY", "sk-test")
        mp.setattr(voicemem, "VoiceMem", FakeVM, raising=False)
        mp.syspath_prepend(str(WEB))
        spec = importlib.util.spec_from_file_location("voicemem_web_run_under_test", WEB / "run.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        mod.TEST_ROOT = Path(root)
        yield mod
    sys.modules.pop("voicemem_web_run_under_test", None)


@pytest.fixture
def as_user():
    tokens = []

    def bind(user):
        tokens.append(tenant.bind(user))

    yield bind
    for t in reversed(tokens):
        tenant.reset(t)


def test_each_user_gets_their_own_memory_instance(run, as_user):
    as_user(A)
    space_a = run.vm.cfg["space"]
    tenant.current.set(None)
    as_user(B)
    space_b = run.vm.cfg["space"]
    assert space_a.startswith(A + "_") and space_b.startswith(B + "_")
    assert space_a != space_b


def test_a_user_cannot_name_another_users_space(run, as_user):
    as_user(A)
    _, scoped = run.space_dir(f"{B}_main")
    assert scoped.startswith(A + "_") and B + "_" in scoped[len(A) + 1:]
    assert run.space_dir("../../etc")[1].startswith(A + "_")
    assert ".." not in run.space_dir("../../etc")[1]


def test_empty_names_are_rejected_for_users(run, as_user):
    as_user(A)
    with pytest.raises(ValueError):
        run.space_dir("   ")
    with pytest.raises(ValueError):
        run.space_dir(f"{A}_")


def test_switching_space_only_changes_your_own_active_space(run, as_user):
    token = tenant.bind(A)
    run.use_space("work")
    assert run.active_space() == f"{A}_work"
    tenant.reset(token)
    token = tenant.bind(B)
    assert run.active_space() == f"{B}_main"
    tenant.reset(token)
    assert run.ACTIVE_SPACE == "demo"          # single-user global untouched


def test_space_list_shows_only_your_spaces_without_the_prefix(run, as_user):
    token = tenant.bind(A)
    run.create_space("diary", "en")
    names = {s["name"] for s in run.list_spaces()}
    tenant.reset(token)
    assert "diary" in names and not any(n.startswith(A) for n in names)
    token = tenant.bind(B)
    assert "diary" not in {s["name"] for s in run.list_spaces()}
    tenant.reset(token)


def test_last_recording_is_not_shared_between_users(run, as_user, tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    token = tenant.bind(A)
    run._remember_tune(str(wav))
    assert run._last_tune_path() == str(wav)
    assert run.audio_of(run.LAST_TUNE_ID) == str(wav)
    tenant.reset(token)
    token = tenant.bind(B)
    assert run._last_tune_path() == ""
    assert run.audio_of(run.LAST_TUNE_ID) == ""
    tenant.reset(token)


def test_recent_hits_and_replay_timer_are_per_user(run):
    token = tenant.bind(A)
    run._hit_ids().add("m1")
    run._REPLAY_UNTIL[run.active_space()] = 10 ** 12
    assert run._replaying_now()
    tenant.reset(token)
    token = tenant.bind(B)
    assert "m1" not in run._hit_ids() and not run._replaying_now()
    tenant.reset(token)


def test_recordings_are_saved_inside_the_users_own_space(run):
    import numpy as np

    token = tenant.bind(A)
    path = Path(run.save_turn_audio(np.zeros(1600, dtype="float32")))
    space_dir = run.space_dir(run.active_space())[0]
    tenant.reset(token)
    assert path.is_file() and space_dir in path.parents


def test_memory_language_is_per_user():
    token = tenant.bind(A)
    lang.set_memory_language("zh")
    tenant.reset(token)
    token = tenant.bind(B)
    lang.set_memory_language("en")
    tenant.reset(token)
    token = tenant.bind(A)
    assert lang.memory_language() == "zh"
    tenant.reset(token)
    token = tenant.bind(B)
    assert lang.memory_language() == "en"
    tenant.reset(token)
    for u in (A, B):
        t = tenant.bind(u)
        lang.set_memory_language(None)
        tenant.reset(t)


def test_concurrent_requests_keep_their_own_user():
    async def request(user, out):
        tenant.bind(user)
        await asyncio.sleep(0.01)                     # let the other request run in between
        out[user] = tenant.current.get()
        out[user + "_thread"] = await asyncio.to_thread(tenant.current.get)

    async def main():
        out = {}
        await asyncio.gather(request(A, out), request(B, out))
        return out

    out = asyncio.run(main())
    assert out == {A: A, A + "_thread": A, B: B, B + "_thread": B}


def test_background_threads_keep_the_user_but_plain_threads_do_not():
    seen = {}
    token = tenant.bind(A)
    t1 = tenant.thread(lambda: seen.__setitem__("kept", tenant.current.get()))
    t2 = threading.Thread(target=lambda: seen.__setitem__("plain", tenant.current.get()))
    for t in (t1, t2):
        t.start()
        t.join()
    tenant.reset(token)
    assert seen == {"kept": A, "plain": None}


def test_bad_tenant_ids_are_refused():
    for bad in ("", "a/b", "../x", "ab", "x" * 40, "a b c d"):
        with pytest.raises(ValueError):
            tenant.bind(bad)
