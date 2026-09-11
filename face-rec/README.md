# RG660MK 人脸识别部署

## 在 Ubuntu 笔记本上（交叉编译）
1. 装依赖: `sudo apt update && sudo apt install -y wget git cmake g++ tar`
2. `bash get_models.sh`        # 下载模型
3. `bash build.sh`             # 交叉编译，产出 face_runner（aarch64 静态）
4. 把产物推到设备:
   scp face_runner models/mnet.25-opt.param models/mnet.25-opt.bin \
       models/mobilefacenet.param models/mobilefacenet.bin root@192.168.1.1:/tmp/

## 在设备上（SSH）
1. 放模型 + 二进制:
   mkdir -p /data/ai_cpe/demo/ai_models/face_detect /data/ai_cpe/demo/ai_models/face_embed
   mv /tmp/mnet.25-opt.* /data/ai_cpe/demo/ai_models/face_detect/
   mv /tmp/mobilefacenet.* /data/ai_cpe/demo/ai_models/face_embed/
   mv /tmp/face_runner /data/ai_cpe/demo/bin/face_runner
   chmod +x /data/ai_cpe/demo/bin/face_runner
2. 更新 ai-service.json 的 models:
   "face_detect": {"family":"retinaface","path":"/data/ai_cpe/demo/ai_models/face_detect/mnet.25-opt.param"},
   "face_embed": {"family":"mobilefacenet","path":"/data/ai_cpe/demo/ai_models/face_embed/mobilefacenet.param"}
3. 改 ai_service.py：face 操作用 face_runner（而非 vision_runner）
4. 重启 ai_service: /etc/init.d/ai_service restart
5. 录入人脸: 对某张照片跑 face_runner，取 embedding 写入 face-gallery.json
6. 测试: python3 /data/ai_cpe/demo/services/hermes_ai_tool.py face --payload '{"input":{"path":"/data/ai_cpe/demo/media/xxx.jpg"}}'
