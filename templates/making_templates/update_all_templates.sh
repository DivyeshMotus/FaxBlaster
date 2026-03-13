#!/bin/bash
echo "Generating all templates..."

python3 t0_rx_template.py
python3 t0_mr_template.py
python3 t0_rx_mr_template.py
python3 t1_rx_template.py
python3 t1_mr_template.py
python3 t1_rx_mr_template.py
python3 t2_rx_template.py
python3 t2_mr_template.py
python3 t2_rx_mr_template.py

echo "All templates generated!"