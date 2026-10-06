# assets

Images and audio assets used by the demos.

## input.wav

Used by the first README example: vm.ingest(audio="input.wav").
It contains “I am vegetarian and allergic to nuts.”, 5.8 seconds, 16 kHz mono PCM16.
One second of silence is kept at the end so the streaming path can detect turn completion.

## question.wav

Used by the streaming README example with vm.stream().
It contains “What are my dietary restrictions?”, 3.2 seconds, 16 kHz mono PCM16.
The final silence allows turn_over to be detected.

## speech.wav

Default input for examples/02_streaming.py.
It contains “I like eating macarons”, 7.7 seconds, 16 kHz mono PCM16.

## cafe_song.wav

Audio used by the web demo when replaying a song heard in a cafe.
15 seconds, 16 kHz mono PCM16. The file is a mix of music and restaurant ambience.
See the original project history for source and licensing details.
