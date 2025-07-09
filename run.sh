#!/bin/bash

python3 make.py
python3 send.py
sleep 2h
python3 failed_faxes.py