# https://micropython.org/download/esp32-ota/

ESPDEV=ttyACM0
esptool.py --port /dev/${ESPDEV} erase_flash
esptool.py --chip esp32 --baud 460800 --port /dev/${ESPDEV} write_flash -z 0x1000 esp32-ota-20230426-v1.20.0.bin

wget https://raw.githubusercontent.com/micropython/micropython-lib/7128d423c2e7c0309ac17a1e6ba873b909b24fcc/micropython/drivers/display/ssd1306/ssd1306.py
