# GIFx

Aka *the poor man's ShadowPlay*. GIFX constantly records your screen, and allows you to capture the last 30 seconds by sending it SIGUSR1.

## Dependencies

In order for GIFx to be functional, you first need to have `ffmpeg` on your computer.

```python
sudo apt install ffmpeg
```

## Usage

First launch the main program (which can also run as a daemon).

```bash
python3 gifx.py
```

Then, whenever you want a GIF of your last 30 seconds to be saved, just send SIGUSR1 to the python program:

```bash
$ kill -USR1 $(ps aux | grep gifx.py | head -n 1 | awk '{ print $2 }')
```

## TODO

- Keep the very first ext block in all generated gifs
- Handle more graciously CTRL+C
