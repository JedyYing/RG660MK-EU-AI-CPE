// kws_server.cpp —— RG660MK 常驻 KWS 唤醒词检测服务（C++ API 版）
//
// 为什么不用命令行版：sherpa-onnx-keyword-spotter 每次调用都要重新加载模型，
// 实测单次 5.4~6.3 s（加载占 5 s），比 ASR 还慢。常驻后加载只做一次。
//
// 用法：kws_server <model_dir> <socket_path>
// 协议：unix socket，客户端发一行 wav 路径，服务端回一行 JSON
//   命中：sherpa 原始 json（含 "keyword"）
//   未命中：{}
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <memory>
#include <unistd.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>

#include "sherpa-onnx/csrc/keyword-spotter.h"
#include "sherpa-onnx/csrc/online-stream.h"
#include "sherpa-onnx/csrc/wave-reader.h"

using namespace sherpa_onnx;

int main(int argc, char **argv) {
  if (argc < 3) {
    fprintf(stderr, "usage: %s <model_dir> <socket_path>\n", argv[0]);
    return 2;
  }
  const std::string dir = argv[1];
  const char *sock_path = argv[2];

  KeywordSpotterConfig config;
  config.model_config.transducer.encoder = dir + "/encoder-epoch-13-avg-2-chunk-16-left-64.int8.onnx";
  config.model_config.transducer.decoder = dir + "/decoder-epoch-13-avg-2-chunk-16-left-64.onnx";
  config.model_config.transducer.joiner = dir + "/joiner-epoch-13-avg-2-chunk-16-left-64.int8.onnx";
  config.model_config.tokens = dir + "/tokens.txt";
  config.model_config.provider_config.provider = "cpu";
  config.model_config.num_threads = 1;
  config.model_config.debug = false;
  config.keywords_file = dir + "/keywords.txt";

  auto kws = std::make_unique<KeywordSpotter>(config);
  fprintf(stderr, "[kws_server] model loaded from %s\n", dir.c_str());

  unlink(sock_path);
  int fd = socket(AF_UNIX, SOCK_STREAM, 0);
  if (fd < 0) { perror("socket"); return 1; }
  struct sockaddr_un addr;
  memset(&addr, 0, sizeof(addr));
  addr.sun_family = AF_UNIX;
  snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", sock_path);
  if (bind(fd, reinterpret_cast<struct sockaddr *>(&addr), sizeof(addr)) < 0) { perror("bind"); return 1; }
  if (listen(fd, 8) < 0) { perror("listen"); return 1; }
  chmod(sock_path, 0666);
  fprintf(stderr, "[kws_server] listening on %s\n", sock_path);

  const std::vector<float> tail(8000, 0.0f);  // 0.5 s 尾部补零

  for (;;) {
    int c = accept(fd, nullptr, nullptr);
    if (c < 0) { if (errno == EINTR) continue; break; }

    char path[512];
    memset(path, 0, sizeof(path));
    ssize_t n = read(c, path, sizeof(path) - 1);
    if (n <= 0) { close(c); continue; }
    while (n > 0 && (path[n - 1] == '\n' || path[n - 1] == '\r')) path[--n] = 0;

    int32_t sr = 0;
    bool ok = false;
    std::vector<float> samples = ReadWave(std::string(path), &sr, &ok);
    if (!ok || samples.empty() || sr <= 0) {
      dprintf(c, "{\"error\":\"read_wave_failed\"}\n");
      close(c);
      continue;
    }

    auto stream = kws->CreateStream();
    bool hit = false;
    if (stream) {
      stream->AcceptWaveform(sr, samples.data(), static_cast<int32_t>(samples.size()));
      stream->AcceptWaveform(sr, tail.data(), static_cast<int32_t>(tail.size()));
      stream->InputFinished();
      while (kws->IsReady(stream.get())) {
        kws->DecodeStream(stream.get());
        KeywordResult r = kws->GetResult(stream.get());
        if (!r.keyword.empty()) {
          dprintf(c, "{\"keyword\":\"%s\",\"start_time\":%.2f}\n", r.keyword.c_str(), r.start_time);
          hit = true;
          kws->Reset(stream.get());
          break;
        }
      }
    }
    if (!hit) dprintf(c, "{}\n");
    close(c);
  }
  close(fd);
  return 0;
}
