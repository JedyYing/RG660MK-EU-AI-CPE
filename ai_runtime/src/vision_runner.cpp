#include <net.h>

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#define STB_IMAGE_IMPLEMENTATION
#define STBI_NO_GIF
#define STBI_NO_HDR
#define STBI_NO_LINEAR
#include "stb_image.h"

namespace {

class JsonError : public std::runtime_error {
public:
    explicit JsonError(const std::string& message) : std::runtime_error(message) {}
};

struct JsonValue {
    enum class Type { Null, Boolean, Number, String, Array, Object };
    Type type = Type::Null;
    bool boolean = false;
    double number = 0.0;
    std::string string;
    std::vector<JsonValue> array;
    std::map<std::string, JsonValue> object;

    const JsonValue& require(const std::string& key) const {
        if (type != Type::Object) throw JsonError("expected object while reading " + key);
        const auto it = object.find(key);
        if (it == object.end()) throw JsonError("missing field: " + key);
        return it->second;
    }

    const JsonValue* optional(const std::string& key) const {
        if (type != Type::Object) return nullptr;
        const auto it = object.find(key);
        return it == object.end() ? nullptr : &it->second;
    }

    std::string as_string(const std::string& field) const {
        if (type != Type::String || string.empty()) throw JsonError(field + " must be a non-empty string");
        return string;
    }

    double as_number(const std::string& field) const {
        if (type != Type::Number || !std::isfinite(number)) throw JsonError(field + " must be a finite number");
        return number;
    }
};

class JsonParser {
public:
    explicit JsonParser(std::string source) : source_(std::move(source)) {}

    JsonValue parse() {
        skip_space();
        JsonValue value = parse_value(0);
        skip_space();
        if (position_ != source_.size()) fail("trailing content");
        return value;
    }

private:
    static constexpr int kMaxDepth = 32;
    std::string source_;
    size_t position_ = 0;

    [[noreturn]] void fail(const std::string& message) const {
        throw JsonError(message + " at byte " + std::to_string(position_));
    }

    void skip_space() {
        while (position_ < source_.size()) {
            const char c = source_[position_];
            if (c != ' ' && c != '\t' && c != '\r' && c != '\n') break;
            ++position_;
        }
    }

    bool consume(char expected) {
        skip_space();
        if (position_ < source_.size() && source_[position_] == expected) {
            ++position_;
            return true;
        }
        return false;
    }

    JsonValue parse_value(int depth) {
        if (depth > kMaxDepth) fail("JSON nesting limit exceeded");
        skip_space();
        if (position_ >= source_.size()) fail("unexpected end of input");
        switch (source_[position_]) {
            case 'n': return parse_literal("null", JsonValue::Type::Null, false);
            case 't': return parse_literal("true", JsonValue::Type::Boolean, true);
            case 'f': return parse_literal("false", JsonValue::Type::Boolean, false);
            case '"': {
                JsonValue value;
                value.type = JsonValue::Type::String;
                value.string = parse_string();
                return value;
            }
            case '[': return parse_array(depth + 1);
            case '{': return parse_object(depth + 1);
            default: return parse_number();
        }
    }

    JsonValue parse_literal(const char* literal, JsonValue::Type type, bool boolean) {
        const size_t length = std::strlen(literal);
        if (source_.compare(position_, length, literal) != 0) fail("invalid literal");
        position_ += length;
        JsonValue value;
        value.type = type;
        value.boolean = boolean;
        return value;
    }

    static void append_utf8(std::string& target, unsigned codepoint) {
        if (codepoint <= 0x7f) {
            target.push_back(static_cast<char>(codepoint));
        } else if (codepoint <= 0x7ff) {
            target.push_back(static_cast<char>(0xc0 | (codepoint >> 6)));
            target.push_back(static_cast<char>(0x80 | (codepoint & 0x3f)));
        } else {
            target.push_back(static_cast<char>(0xe0 | (codepoint >> 12)));
            target.push_back(static_cast<char>(0x80 | ((codepoint >> 6) & 0x3f)));
            target.push_back(static_cast<char>(0x80 | (codepoint & 0x3f)));
        }
    }

    std::string parse_string() {
        if (!consume('"')) fail("expected string");
        std::string result;
        while (position_ < source_.size()) {
            const unsigned char c = static_cast<unsigned char>(source_[position_++]);
            if (c == '"') return result;
            if (c < 0x20) fail("control character in string");
            if (c != '\\') {
                result.push_back(static_cast<char>(c));
                continue;
            }
            if (position_ >= source_.size()) fail("unfinished escape");
            const char escaped = source_[position_++];
            switch (escaped) {
                case '"': result.push_back('"'); break;
                case '\\': result.push_back('\\'); break;
                case '/': result.push_back('/'); break;
                case 'b': result.push_back('\b'); break;
                case 'f': result.push_back('\f'); break;
                case 'n': result.push_back('\n'); break;
                case 'r': result.push_back('\r'); break;
                case 't': result.push_back('\t'); break;
                case 'u': {
                    if (position_ + 4 > source_.size()) fail("short unicode escape");
                    unsigned codepoint = 0;
                    for (int i = 0; i < 4; ++i) {
                        const char hex = source_[position_++];
                        codepoint <<= 4;
                        if (hex >= '0' && hex <= '9') codepoint |= static_cast<unsigned>(hex - '0');
                        else if (hex >= 'a' && hex <= 'f') codepoint |= static_cast<unsigned>(hex - 'a' + 10);
                        else if (hex >= 'A' && hex <= 'F') codepoint |= static_cast<unsigned>(hex - 'A' + 10);
                        else fail("invalid unicode escape");
                    }
                    if (codepoint >= 0xd800 && codepoint <= 0xdfff) fail("surrogate escapes are unsupported");
                    append_utf8(result, codepoint);
                    break;
                }
                default: fail("invalid escape");
            }
        }
        fail("unterminated string");
    }

    JsonValue parse_number() {
        const char* start = source_.c_str() + position_;
        char* end = nullptr;
        errno = 0;
        const double parsed = std::strtod(start, &end);
        if (end == start || errno == ERANGE || !std::isfinite(parsed)) fail("invalid number");
        position_ += static_cast<size_t>(end - start);
        JsonValue value;
        value.type = JsonValue::Type::Number;
        value.number = parsed;
        return value;
    }

    JsonValue parse_array(int depth) {
        if (!consume('[')) fail("expected array");
        JsonValue value;
        value.type = JsonValue::Type::Array;
        if (consume(']')) return value;
        for (;;) {
            value.array.push_back(parse_value(depth));
            if (consume(']')) return value;
            if (!consume(',')) fail("expected comma in array");
        }
    }

    JsonValue parse_object(int depth) {
        if (!consume('{')) fail("expected object");
        JsonValue value;
        value.type = JsonValue::Type::Object;
        if (consume('}')) return value;
        for (;;) {
            skip_space();
            if (position_ >= source_.size() || source_[position_] != '"') fail("expected object key");
            std::string key = parse_string();
            if (!consume(':')) fail("expected colon");
            if (!value.object.emplace(key, parse_value(depth)).second) fail("duplicate object key");
            if (consume('}')) return value;
            if (!consume(',')) fail("expected comma in object");
        }
    }
};

std::string json_escape(const std::string& value) {
    std::ostringstream output;
    for (const unsigned char c : value) {
        switch (c) {
            case '"': output << "\\\""; break;
            case '\\': output << "\\\\"; break;
            case '\b': output << "\\b"; break;
            case '\f': output << "\\f"; break;
            case '\n': output << "\\n"; break;
            case '\r': output << "\\r"; break;
            case '\t': output << "\\t"; break;
            default:
                if (c < 0x20) {
                    output << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(c)
                           << std::dec << std::setfill(' ');
                } else {
                    output << static_cast<char>(c);
                }
        }
    }
    return output.str();
}

void emit_error(const std::string& code, const std::string& message) {
    std::cout << "{\"ok\":false,\"error\":{\"code\":\"" << json_escape(code)
              << "\",\"message\":\"" << json_escape(message) << "\"}}";
}

struct Options {
    float confidence = 0.25f;
    float iou = 0.45f;
    int max_detections = 100;
    int num_threads = 4;
    bool agnostic_nms = false;
    std::vector<int> classes;
};

int bounded_integer(const JsonValue& value, const std::string& field, int minimum, int maximum) {
    const double number = value.as_number(field);
    if (std::floor(number) != number || number < minimum || number > maximum) {
        throw JsonError(field + " must be an integer from " + std::to_string(minimum) + " to " + std::to_string(maximum));
    }
    return static_cast<int>(number);
}

float bounded_float(const JsonValue& value, const std::string& field) {
    const double number = value.as_number(field);
    if (number < 0.0 || number > 1.0) throw JsonError(field + " must be between 0 and 1");
    return static_cast<float>(number);
}

Options parse_options(const JsonValue* value) {
    Options options;
    if (!value) return options;
    if (value->type != JsonValue::Type::Object) throw JsonError("options must be an object");
    if (const JsonValue* item = value->optional("confidence")) options.confidence = bounded_float(*item, "options.confidence");
    if (const JsonValue* item = value->optional("iou")) options.iou = bounded_float(*item, "options.iou");
    if (const JsonValue* item = value->optional("max_detections")) {
        options.max_detections = bounded_integer(*item, "options.max_detections", 1, 300);
    }
    if (const JsonValue* item = value->optional("num_threads")) {
        options.num_threads = bounded_integer(*item, "options.num_threads", 1, 8);
    }
    if (const JsonValue* item = value->optional("agnostic_nms")) {
        if (item->type != JsonValue::Type::Boolean) throw JsonError("options.agnostic_nms must be boolean");
        options.agnostic_nms = item->boolean;
    }
    if (const JsonValue* item = value->optional("classes")) {
        if (item->type != JsonValue::Type::Array) throw JsonError("options.classes must be an array");
        for (const JsonValue& entry : item->array) {
            options.classes.push_back(bounded_integer(entry, "options.classes[]", 0, 79));
        }
        std::sort(options.classes.begin(), options.classes.end());
        options.classes.erase(std::unique(options.classes.begin(), options.classes.end()), options.classes.end());
    }
    return options;
}

struct ImageData {
    int width = 0;
    int height = 0;
    unsigned char* pixels = nullptr;
    ~ImageData() { stbi_image_free(pixels); }
    ImageData(const ImageData&) = delete;
    ImageData& operator=(const ImageData&) = delete;
    ImageData(ImageData&& other) noexcept
        : width(other.width), height(other.height), pixels(other.pixels) {
        other.pixels = nullptr;
    }
    ImageData& operator=(ImageData&& other) noexcept {
        if (this != &other) {
            stbi_image_free(pixels);
            width = other.width;
            height = other.height;
            pixels = other.pixels;
            other.pixels = nullptr;
        }
        return *this;
    }
    ImageData() = default;
};

ImageData load_image(const std::string& path) {
    int width = 0;
    int height = 0;
    int channels = 0;
    if (!stbi_info(path.c_str(), &width, &height, &channels)) {
        throw std::runtime_error("cannot read image header: " + std::string(stbi_failure_reason() ? stbi_failure_reason() : "unknown error"));
    }
    constexpr int kMaxDimension = 8192;
    constexpr long long kMaxPixels = 40000000;
    if (width <= 0 || height <= 0 || width > kMaxDimension || height > kMaxDimension ||
        static_cast<long long>(width) * height > kMaxPixels) {
        throw std::runtime_error("image dimensions exceed runtime limits");
    }
    ImageData image;
    image.pixels = stbi_load(path.c_str(), &image.width, &image.height, &channels, 3);
    if (!image.pixels) {
        throw std::runtime_error("cannot decode image: " + std::string(stbi_failure_reason() ? stbi_failure_reason() : "unknown error"));
    }
    return image;
}

struct Letterbox {
    ncnn::Mat tensor;
    float gain = 1.0f;
    int left = 0;
    int top = 0;
};

Letterbox make_input(const ImageData& image, int target_width, int target_height) {
    Letterbox result;
    result.gain = std::min(static_cast<float>(target_width) / image.width,
                           static_cast<float>(target_height) / image.height);
    const int resized_width = std::max(1, static_cast<int>(std::round(image.width * result.gain)));
    const int resized_height = std::max(1, static_cast<int>(std::round(image.height * result.gain)));
    result.left = (target_width - resized_width) / 2;
    result.top = (target_height - resized_height) / 2;
    const int right = target_width - resized_width - result.left;
    const int bottom = target_height - resized_height - result.top;

    ncnn::Mat resized = ncnn::Mat::from_pixels_resize(
        image.pixels, ncnn::Mat::PIXEL_RGB, image.width, image.height, resized_width, resized_height);
    ncnn::copy_make_border(resized, result.tensor, result.top, bottom, result.left, right,
                           ncnn::BORDER_CONSTANT, 114.0f);
    const float normalization[3] = {1.0f / 255.0f, 1.0f / 255.0f, 1.0f / 255.0f};
    result.tensor.substract_mean_normalize(nullptr, normalization);
    return result;
}

struct Keypoint {
    float x = 0;
    float y = 0;
    float score = 0;
};

struct Detection {
    float x1 = 0;
    float y1 = 0;
    float x2 = 0;
    float y2 = 0;
    float score = 0;
    int class_id = 0;
    std::vector<Keypoint> keypoints;
};

float intersection_over_union(const Detection& first, const Detection& second) {
    const float left = std::max(first.x1, second.x1);
    const float top = std::max(first.y1, second.y1);
    const float right = std::min(first.x2, second.x2);
    const float bottom = std::min(first.y2, second.y2);
    const float intersection = std::max(0.0f, right - left) * std::max(0.0f, bottom - top);
    const float first_area = std::max(0.0f, first.x2 - first.x1) * std::max(0.0f, first.y2 - first.y1);
    const float second_area = std::max(0.0f, second.x2 - second.x1) * std::max(0.0f, second.y2 - second.y1);
    const float union_area = first_area + second_area - intersection;
    return union_area > 0.0f ? intersection / union_area : 0.0f;
}

std::vector<Detection> non_maximum_suppression(std::vector<Detection> candidates, const Options& options) {
    std::sort(candidates.begin(), candidates.end(), [](const Detection& a, const Detection& b) {
        return a.score > b.score;
    });
    std::vector<Detection> selected;
    selected.reserve(std::min(static_cast<size_t>(options.max_detections), candidates.size()));
    for (Detection& candidate : candidates) {
        bool keep = true;
        for (const Detection& accepted : selected) {
            if (!options.agnostic_nms && candidate.class_id != accepted.class_id) continue;
            if (intersection_over_union(candidate, accepted) > options.iou) {
                keep = false;
                break;
            }
        }
        if (keep) {
            selected.push_back(std::move(candidate));
            if (static_cast<int>(selected.size()) >= options.max_detections) break;
        }
    }
    return selected;
}

bool class_allowed(int class_id, const Options& options) {
    return options.classes.empty() || std::binary_search(options.classes.begin(), options.classes.end(), class_id);
}

class OutputMatrix {
public:
    explicit OutputMatrix(const ncnn::Mat& output) : output_(output) {
        if (output.dims == 2) {
            width_ = output.w;
            rows_ = output.h;
        } else if (output.dims == 3 && output.c == 1) {
            width_ = output.w;
            rows_ = output.h;
        } else {
            throw std::runtime_error("unexpected NCNN output rank");
        }
    }

    int width() const { return width_; }
    int rows() const { return rows_; }
    const float* row(int index) const {
        if (output_.dims == 2) return output_.row(index);
        return output_.channel(0).row(index);
    }

private:
    const ncnn::Mat& output_;
    int width_ = 0;
    int rows_ = 0;
};

float restore_x(float coordinate, const Letterbox& letterbox, int image_width) {
    return std::clamp((coordinate - letterbox.left) / letterbox.gain, 0.0f, static_cast<float>(image_width));
}

float restore_y(float coordinate, const Letterbox& letterbox, int image_height) {
    return std::clamp((coordinate - letterbox.top) / letterbox.gain, 0.0f, static_cast<float>(image_height));
}

std::vector<Detection> decode_output(const ncnn::Mat& raw_output, bool pose, const Options& options,
                                     const Letterbox& letterbox, int image_width, int image_height) {
    const OutputMatrix output(raw_output);
    const int expected_rows = pose ? 56 : 84;
    if (output.rows() != expected_rows || output.width() != 8400) {
        throw std::runtime_error("unexpected NCNN output shape: rows=" + std::to_string(output.rows()) +
                                 " width=" + std::to_string(output.width()));
    }

    const int class_count = pose ? 1 : 80;
    std::vector<Detection> candidates;
    for (int proposal = 0; proposal < output.width(); ++proposal) {
        int class_id = 0;
        float score = output.row(4)[proposal];
        for (int class_index = 1; class_index < class_count; ++class_index) {
            const float candidate_score = output.row(4 + class_index)[proposal];
            if (candidate_score > score) {
                score = candidate_score;
                class_id = class_index;
            }
        }
        if (score < options.confidence || !class_allowed(class_id, options)) continue;

        const float center_x = output.row(0)[proposal];
        const float center_y = output.row(1)[proposal];
        const float box_width = output.row(2)[proposal];
        const float box_height = output.row(3)[proposal];
        Detection detection;
        detection.x1 = restore_x(center_x - box_width * 0.5f, letterbox, image_width);
        detection.y1 = restore_y(center_y - box_height * 0.5f, letterbox, image_height);
        detection.x2 = restore_x(center_x + box_width * 0.5f, letterbox, image_width);
        detection.y2 = restore_y(center_y + box_height * 0.5f, letterbox, image_height);
        detection.score = score;
        detection.class_id = class_id;

        if (pose) {
            detection.keypoints.reserve(17);
            for (int index = 0; index < 17; ++index) {
                const int base = 5 + index * 3;
                detection.keypoints.push_back({
                    restore_x(output.row(base)[proposal], letterbox, image_width),
                    restore_y(output.row(base + 1)[proposal], letterbox, image_height),
                    output.row(base + 2)[proposal],
                });
            }
        }
        candidates.push_back(std::move(detection));
    }
    return non_maximum_suppression(std::move(candidates), options);
}

std::string model_bin_path(const std::string& parameter_path) {
    const std::string suffix = ".param";
    if (parameter_path.size() <= suffix.size() ||
        parameter_path.compare(parameter_path.size() - suffix.size(), suffix.size(), suffix) != 0) {
        throw JsonError("model path must end in .param");
    }
    return parameter_path.substr(0, parameter_path.size() - suffix.size()) + ".bin";
}

const char* const kCocoNames[] = {
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat", "traffic light",
    "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep", "cow",
    "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove", "skateboard", "surfboard",
    "tennis racket", "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote", "keyboard",
    "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase", "scissors",
    "teddy bear", "hair drier", "toothbrush"
};

void emit_box(std::ostream& output, const Detection& detection) {
    output << "{\"x1\":" << detection.x1 << ",\"y1\":" << detection.y1
           << ",\"x2\":" << detection.x2 << ",\"y2\":" << detection.y2 << '}';
}

void emit_success(const std::vector<Detection>& detections, bool pose, const ImageData& image, double inference_ms) {
    std::cout << std::fixed << std::setprecision(4);
    std::cout << "{\"ok\":true,\"result\":{\"image\":{\"width\":" << image.width
              << ",\"height\":" << image.height << "},\"inference_ms\":" << inference_ms << ',';
    if (pose) {
        std::cout << "\"persons\":[";
        for (size_t index = 0; index < detections.size(); ++index) {
            if (index) std::cout << ',';
            const Detection& detection = detections[index];
            std::cout << "{\"bbox\":";
            emit_box(std::cout, detection);
            std::cout << ",\"score\":" << detection.score << ",\"keypoints\":[";
            for (size_t keypoint_index = 0; keypoint_index < detection.keypoints.size(); ++keypoint_index) {
                if (keypoint_index) std::cout << ',';
                const Keypoint& keypoint = detection.keypoints[keypoint_index];
                std::cout << "{\"x\":" << keypoint.x << ",\"y\":" << keypoint.y
                          << ",\"score\":" << keypoint.score << '}';
            }
            std::cout << "]}";
        }
        std::cout << ']';
    } else {
        std::cout << "\"detections\":[";
        for (size_t index = 0; index < detections.size(); ++index) {
            if (index) std::cout << ',';
            const Detection& detection = detections[index];
            std::cout << "{\"bbox\":";
            emit_box(std::cout, detection);
            std::cout << ",\"score\":" << detection.score << ",\"class_id\":" << detection.class_id
                      << ",\"class_name\":\"" << kCocoNames[detection.class_id] << "\"}";
        }
        std::cout << ']';
    }
    std::cout << ",\"count\":" << detections.size() << "}}";
}

void run() {
    std::ostringstream input_buffer;
    input_buffer << std::cin.rdbuf();
    const std::string input_json = input_buffer.str();
    if (input_json.empty() || input_json.size() > 1024 * 1024) throw JsonError("request must be 1 byte to 1 MiB");
    const JsonValue request = JsonParser(input_json).parse();
    if (request.type != JsonValue::Type::Object) throw JsonError("request must be an object");

    const int version = bounded_integer(request.require("version"), "version", 1, 1);
    (void)version;
    const std::string operation = request.require("operation").as_string("operation");
    if (operation != "detect" && operation != "pose") throw JsonError("operation must be detect or pose");
    const bool pose = operation == "pose";
    const std::string image_path = request.require("input").require("path").as_string("input.path");
    const JsonValue& models = request.require("models");
    const std::string model_path = models.require(pose ? "pose" : "detect").as_string("models path");
    const std::string binary_path = model_bin_path(model_path);
    const Options options = parse_options(request.optional("options"));

    ImageData image = load_image(image_path);
    Letterbox letterbox = make_input(image, 640, 640);

    ncnn::Net network;
    network.opt.use_vulkan_compute = false;
    network.opt.use_fp16_packed = false;
    network.opt.use_fp16_storage = false;
    network.opt.use_fp16_arithmetic = false;
    network.opt.use_packing_layout = false;
    network.opt.num_threads = options.num_threads;
    if (network.load_param(model_path.c_str()) != 0) throw std::runtime_error("failed to load NCNN model parameters");
    if (network.load_model(binary_path.c_str()) != 0) throw std::runtime_error("failed to load NCNN model weights");

    ncnn::Extractor extractor = network.create_extractor();
    if (extractor.input("in0", letterbox.tensor) != 0) throw std::runtime_error("failed to set NCNN input blob in0");
    ncnn::Mat raw_output;
    const auto started = std::chrono::steady_clock::now();
    if (extractor.extract("out0", raw_output) != 0) throw std::runtime_error("failed to extract NCNN output blob out0");
    const auto finished = std::chrono::steady_clock::now();
    const double inference_ms = std::chrono::duration<double, std::milli>(finished - started).count();

    const std::vector<Detection> detections = decode_output(
        raw_output, pose, options, letterbox, image.width, image.height);
    emit_success(detections, pose, image, inference_ms);
}

}  // namespace

int main() {
    try {
        run();
    } catch (const JsonError& error) {
        emit_error("INVALID_ARGUMENT", error.what());
    } catch (const std::bad_alloc&) {
        emit_error("RESOURCE_LIMIT", "vision runner ran out of memory");
    } catch (const std::exception& error) {
        emit_error("RUNTIME_UNAVAILABLE", error.what());
    }
    return 0;
}
