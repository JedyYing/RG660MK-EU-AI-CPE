#!/bin/bash
# get_models.sh — 下载人脸模型
set -e
mkdir -p models
cd models
echo "下载 RetinaFace 检测模型..."
wget -q https://github.com/nihui/ncnn-assets/raw/master/models/mnet.25-opt.param
wget -q https://github.com/nihui/ncnn-assets/raw/master/models/mnet.25-opt.bin
echo "下载 MobileFaceNet 特征模型..."
wget -q https://github.com/GRAYKEY/mobilefacenet_ncnn/raw/master/models/mobilefacenet.param
wget -q https://github.com/GRAYKEY/mobilefacenet_ncnn/raw/master/models/mobilefacenet.bin
ls -la
echo "✅ 模型就绪"
