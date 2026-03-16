#!/bin/bash
source /home/dved/FaxBlaster/FaxBlasterEnvironment/bin/activate 
python3 make.py
sleep 1m
python3 send.py
