cmake_minimum_required(VERSION 3.10)
project(rg660mk_pose CXX C)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# 必需外部路径(由命令行 -D 传入):
#   NCNN_ROOT   : 含 include/ncnn 与 lib/libncnn.a 的安装前缀
#   STB_DIR     : 含 stb_image.h / stb_image_write.h
#   UVC_INC/UVC_LIB/USB_LIB : 摄像头入口需要(aarch64)
if(NOT NCNN_ROOT)
  message(FATAL_ERROR "请传入 -DNCNN_ROOT=<ncnn install prefix>")
endif()
if(NOT STB_DIR)
  set(STB_DIR "${CMAKE_CURRENT_SOURCE_DIR}/third_party")
endif()

include_directories(
  ${CMAKE_CURRENT_SOURCE_DIR}/include
  ${NCNN_ROOT}/include/ncnn
  ${STB_DIR}
)

set(NCNN_LIB ${NCNN_ROOT}/lib/libncnn.a)

# 核心库
add_library(rgpose STATIC pose_detector.cpp)
target_link_libraries(rgpose ${NCNN_LIB})

find_package(Threads REQUIRED)

# 静态图片入口(host 与 aarch64 都编)
add_executable(pose_image apps/app_image.cpp)
target_link_libraries(pose_image rgpose ${NCNN_LIB} Threads::Threads)

# 摄像头入口(需要 libuvc/libusb;host 若无则用 BUILD_CAMERA=OFF 跳过)
option(BUILD_CAMERA "Build camera app (needs libuvc/libusb)" ON)
if(BUILD_CAMERA)
  if(NOT UVC_INC OR NOT UVC_LIB OR NOT USB_LIB)
    message(WARNING "缺 UVC_INC/UVC_LIB/USB_LIB,跳过 pose_camera")
  else()
    add_executable(pose_camera apps/app_camera.cpp)
    target_include_directories(pose_camera PRIVATE ${UVC_INC})
    target_link_libraries(pose_camera rgpose ${NCNN_LIB} ${UVC_LIB} ${USB_LIB} Threads::Threads)
  endif()
endif()
