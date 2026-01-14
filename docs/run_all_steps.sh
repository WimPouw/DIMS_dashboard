#!/bin/bash

# Motion Tracking Pipeline Runner

echo "=================================="
echo "Motion Tracking Pipeline"
echo "=================================="
echo ""

# Activate conda environment
echo "Activating conda environment..."
eval "$(conda shell.bash hook)"
conda activate my_env
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to activate conda environment!"
    read -p "Press any key to continue..."
    exit 1
fi
echo "Conda environment activated!"


echo "Press any key to start..."
read -n 1 -s
echo ""

# Step 4
echo "[4/4] Running cross-wavelet analysis..."
python3 optional_step_crosswavelet.py
if [ $? -ne 0 ]; then
    echo "ERROR: Step 4 failed!"
    read -p "Press any key to continue..."
    exit 1
fi
echo "Step 4 complete!"
echo ""

echo "=================================="
echo "Pipeline complete!"
echo "=================================="
echo ""
read -p "Press any key to continue..."