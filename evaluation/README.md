# Evaluation

Run the benchmark:

    export OPENAI_API_KEY=sk-...
    python evaluation/run.py --dataset locomo --data data/locomo.json

Results are printed and saved to results/locomo.json.

## Common parameters

| Parameter | Purpose | Default |
|---|---|---|
| --dataset / --data | Adapter and data file | Required |
| --answer-model | Model that answers using memory | gpt-4o-mini |
| --judge | Judge model | gpt-4o-mini |
| --top-k | Memories retrieved per question | 5 |
| --mode | left_brain_single or text_mode | left_brain_single |
| --workers | Concurrent conversations | 4 |
| --limit | First N conversations only | All |
| --resume | Continue a previous run | Off |
| --save-memory | Save retrieved memories for inspection | Off |
| --inspect | Parse only; do not run evaluation | Off |
| --no-score | Generate answers without scoring | Off |

Each completed conversation is written to disk, so --resume can continue an interrupted run.

## Re-score results

    python evaluation/score.py --file results/locomo.json --judge gpt-4o

Retrieval and answering are the expensive part. Re-scoring lets you change the judge or rubric without rerunning retrieval.

## Result file

The result contains summary, configuration, provenance, and per-question gold/predicted answers and scoring notes.

The provenance block records the Git commit, working-tree state, Python version, and package versions.

## Add a benchmark

Create a dataset adapter with load() and score() functions, register it in datasets/__init__.py, then run evaluation/run.py with the new dataset name.
