---
title: CAN Bus Enabled
---

# CAN Bus Enabled

> **Warning**: Setting up CAN bus and flashing MCUs over a CAN bus can be complicated. If you are unsure if you can do this with out help, it would be better off to use additional MCUs via USB.

## Overview

By default CAN bus is enabled to run at 1 Mhz, with 128 deep txqueuelen. These values have been picked since they are the same values as described in [esoterical CAN Bus guide](https://canbus.esoterical.online/Getting_Started.html). 

Currently there is an onboard CAN bus chip that is connected to the rear connector, but currently we have not been able to get this to work.

To be able to use CAN bus on your printer you will need to do one of the following:  
- Use a USB to CAN bus adapter:
    - [PiCAN from Isik's Tech](https://store.isiks.tech/products/pican-usb-to-can-bus-adapter)
    - [U2C by BTT](https://global.bttwiki.com/U2C.html)
- Flash an MCU into USB to CAN bridge mode, [esoterical guide](https://canbus.esoterical.online/USB_CAN_Bridge_Mainboard.html) on how to do this.

## Flashing
For flashing MCUs please follow esoterical CAN Bus [toolhead flashing](https://canbus.esoterical.online/toolhead_flashing.html) guide, but instead of using mainline Klipper to flash to your MCU, [U1-Klipper](https://github.com/Snapmaker/u1-klipper) needs to be used instead. You will not be able to do this flashing on your U1, and needs to be done on something else like a raspberry pi.

## Klipper Config
Once your MCU is flashed and your CAN device connected to your U1, you should be able to verify that you see your MCU device show up. To query and verify, ssh into your printer and run:  
```
python klipper/scripts/canbus_query.py can1
```  

CAN1 needs to be queried since CAN0 is the onboard CAN bus chip. Once you found the UUID add your MCU to your klipper config. Should look something like the following below
```
[mcu extra_mcu]
canbus_uuid: 3c1fd0b940d8
canbus_interface: can1
```