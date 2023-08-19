ampy --port /dev/ttyACM0 put aioble aioble
for f in `ls ~/work/git/micropython-lib/micropython/bluetooth/aioble/aioble/`; do echo "Uploading $f"; ampy --port /dev/ttyACM0 put ~/work/git/micropython-lib/micropython/bluetooth/aioble/aioble/$f aioble/$f; done
ampy --port /dev/ttyACM0 put ssd1306.py ssd1306.py
ampy --port /dev/ttyACM0 put bikestats.py bikestats.py
ampy --port /dev/ttyACM0 put main.py main.py
