// 用 macOS Vision 框架做图片文字识别，输出 JSON 数组。
// 用法：./ocr_bin <图片路径> [更多图片...]
// 输出：[{"file": "...", "text": "..."} | {"file": "...", "error": "..."}]

import Foundation
import Vision
import ImageIO

let args = Array(CommandLine.arguments.dropFirst())
var out: [[String: Any]] = []

for path in args {
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
          let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
        out.append(["file": path, "error": "cannot decode image"])
        continue
    }

    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["zh-Hans", "en-US"]
    req.usesLanguageCorrection = true

    let handler = VNImageRequestHandler(cgImage: img, options: [:])
    do {
        try handler.perform([req])
        let lines = (req.results ?? []).compactMap { $0.topCandidates(1).first?.string }
        out.append(["file": path, "text": lines.joined(separator: "\n")])
    } catch {
        out.append(["file": path, "error": "\(error)"])
    }
}

let data = try JSONSerialization.data(withJSONObject: out,
                                      options: [.prettyPrinted, .withoutEscapingSlashes])
print(String(data: data, encoding: .utf8)!)
