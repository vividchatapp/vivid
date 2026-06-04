#!/bin/bash

# Start the first instance with prefix 'vivid1' in the background
./venv/bin/python vivid.py vivid1 &

# Start the second instance with prefix 'vivid2' in the background
./venv/bin/python vivid.py vivid2 &

echo "Vivid instances (vivid1 and vivid2) started in the background."