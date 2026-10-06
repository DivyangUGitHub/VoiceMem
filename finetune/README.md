# Finetuning

Train your own VoiceMem reply adapter.

    pip install ms-swift==4.5.2 bitsandbytes
    python finetune/train.py
    python finetune/train.py --data data/train.jsonl

Default hyperparameters are defined in finetune/utils.py and match the released adapter configuration.

## Files

| File | Purpose |
|---|---|
| train.py | Training entry point |
| eval.py | Evaluate a trained adapter |
| dataset.py | Read and validate JSONL data |
| utils.py | Hyperparameters, prompts, and warmup calculation |
| data/sample.jsonl | Sample training data |

## Data format

The JSONL file contains messages plus metadata such as language, category, session ID, and turn number.
Memory context is appended to the final user turn, and only the final assistant turn contributes to the training loss.

## Common parameters

    python finetune/train.py --data data/train.jsonl --out out/my-adapter --epochs 3 --lr 1e-4 --no-4bit

| Parameter | Purpose | Default |
|---|---|---|
| --data | Training data | finetune/data/sample.jsonl |
| --out | Output directory | out/voicemem-qlora |
| --base | Base model | Qwen/Qwen3.6-35B-A3B |
| --rank / --alpha | LoRA rank / alpha | 32 / 64 |
| --epochs / --lr | Epochs / learning rate | 2 / 2e-4 |
| --max-len | Maximum sequence length | 2048 |
| --no-4bit | Disable 4-bit quantization | 4-bit enabled |

When changing the base model, update target_regex in utils.py to match its module names.

## Evaluation

    python finetune/eval.py --adapter out/voicemem-qlora --out preds.jsonl

Temperature is fixed at 0 for reproducible comparisons.
