cmake_minimum_required(VERSION 3.16)
project(rg660mk_vision_runner LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(NCNN_BUILD_CONFIG_DIR "" CACHE PATH "Directory containing ncnnConfig.cmake")
set(STB_INCLUDE_DIR "" CACHE PATH "Directory containing stb_image.h")

if(NOT NCNN_BUILD_CONFIG_DIR)
    message(FATAL_ERROR "NCNN_BUILD_CONFIG_DIR is required")
endif()
if(NOT EXISTS "${STB_INCLUDE_DIR}/stb_image.h")
    message(FATAL_ERROR "STB_INCLUDE_DIR must contain stb_image.h")
endif()

include("${NCNN_BUILD_CONFIG_DIR}/ncnnConfig.cmake")
if(NOT TARGET ncnn)
    message(FATAL_ERROR "ncnn target was not provided by ${NCNN_BUILD_CONFIG_DIR}")
endif()

add_executable(vision_runner src/vision_runner.cpp)
target_include_directories(vision_runner PRIVATE "${STB_INCLUDE_DIR}")
target_link_libraries(vision_runner PRIVATE ncnn)
target_compile_options(vision_runner PRIVATE -Wall -Wextra -Wpedantic)
install(TARGETS vision_runner RUNTIME DESTINATION bin)
