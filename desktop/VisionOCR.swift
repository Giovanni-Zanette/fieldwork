import Foundation
import Vision
import ImageIO
import CoreImage

// Offline English OCR. All returned boxes use the upright image's top-left origin.
// Confidence is Vision's score, not a probability of correctness.
struct OCRWord: Codable { let text: String; let bbox: [Double]; let confidence: Float }
struct OCRLine: Codable { let text: String; let bbox: [Double]; let confidence: Float; let words: [OCRWord] }
struct OCRResult: Codable {
    let version: Int; let source: String; let width: Int; let height: Int
    let rotation: Int; let language: String; let observations: [OCRLine]
    let confidence: Double; let requiresReview: Bool
}
func fail(_ message: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8)); exit(code)
}
func box(_ rect: CGRect) -> [Double] {
    [max(0, Double(rect.minX)), max(0, 1-Double(rect.maxY)), min(1, Double(rect.maxX)), min(1, 1-Double(rect.minY))]
}
let args = Array(CommandLine.arguments.dropFirst())
guard let input = args.first, !input.hasPrefix("--") else { fail("Usage: fieldwork-ocr IMAGE --normalized-output OUTPUT.png") }
let output: String? = args.firstIndex(of: "--normalized-output").flatMap { $0+1 < args.count ? args[$0+1] : nil }
let inputURL = URL(fileURLWithPath: input)
guard let src = CGImageSourceCreateWithURL(inputURL as CFURL, nil) else { fail("The image could not be decoded.") }
let metadata = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as? [CFString: Any]
guard let pixelWidth = (metadata?[kCGImagePropertyPixelWidth] as? NSNumber)?.intValue,
      let pixelHeight = (metadata?[kCGImagePropertyPixelHeight] as? NSNumber)?.intValue,
      pixelWidth > 0, pixelHeight > 0, pixelWidth <= 16000, pixelHeight <= 16000,
      pixelWidth * pixelHeight <= 40_000_000 else { fail("Image exceeds the 40 megapixel OCR limit or has invalid dimensions.") }
guard let original = CGImageSourceCreateImageAtIndex(src, 0, nil) else { fail("The image could not be decoded.") }
let exif = (metadata?[kCGImagePropertyOrientation] as? NSNumber)?.int32Value ?? 1
let context = CIContext(options: [.useSoftwareRenderer: false])
let base = CIImage(cgImage: original).oriented(forExifOrientation: exif)
guard let baseImage = context.createCGImage(base, from: base.extent) else { fail("The image orientation could not be normalized.") }
let orientations: [(CGImagePropertyOrientation, Int)] = [(.up,0),(.right,90),(.down,180),(.left,270)]
var bestLines: [OCRLine] = []; var bestScore = -1.0; var bestRotation = 0; var bestOrientation = CGImagePropertyOrientation.up
for (orientation, degrees) in orientations {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["en-US"]
    request.usesLanguageCorrection = false
    request.minimumTextHeight = 0.002
    request.revision = VNRecognizeTextRequestRevision3
    do { try VNImageRequestHandler(cgImage: baseImage, orientation: orientation, options: [:]).perform([request]) }
    catch { fail("Offline text recognition failed. Try a clearer image.") }
    var lines: [OCRLine] = []
    var uprightScore = 0.0
    for observation in request.results ?? [] {
        guard let candidate = observation.topCandidates(1).first, !candidate.string.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { continue }
        let text = candidate.string
        let dx = observation.topRight.x - observation.topLeft.x
        let dy = observation.topRight.y - observation.topLeft.y
        // Vision can read upside-down/sideways text at high confidence. Use its
        // directed quadrilateral, not confidence alone, to choose upright layout.
        if dx > abs(dy) * 2 && observation.topLeft.y > observation.bottomLeft.y {
            uprightScore += Double(text.filter { $0.isLetter || $0.isNumber }.count) * pow(Double(candidate.confidence), 2)
        }
        var words: [OCRWord] = []
        let regex = try! NSRegularExpression(pattern: "\\S+")
        for match in regex.matches(in: text, range: NSRange(text.startIndex..., in: text)) {
            guard let range = Range(match.range, in: text), let rectangle = try? candidate.boundingBox(for: range) else { continue }
            words.append(OCRWord(text: String(text[range]), bbox: box(rectangle.boundingBox), confidence: candidate.confidence))
        }
        lines.append(OCRLine(text: text, bbox: box(observation.boundingBox), confidence: candidate.confidence, words: words))
    }
    let score = uprightScore
    if score > bestScore { bestScore = score; bestLines = lines; bestRotation = degrees; bestOrientation = orientation }
}
let upright = CIImage(cgImage: baseImage).oriented(bestOrientation)
guard let normalized = context.createCGImage(upright, from: upright.extent) else { fail("The OCR image could not be rendered.") }
if let output {
    let outputURL = URL(fileURLWithPath: output)
    guard let destination = CGImageDestinationCreateWithURL(outputURL as CFURL, "public.png" as CFString, 1, nil) else { fail("The normalized image could not be saved.") }
    CGImageDestinationAddImage(destination, normalized, nil)
    guard CGImageDestinationFinalize(destination) else { fail("The normalized image could not be saved.") }
}
bestLines.sort { a,b in abs(a.bbox[1]-b.bbox[1]) > 0.008 ? a.bbox[1] < b.bbox[1] : a.bbox[0] < b.bbox[0] }
let mean = bestLines.isEmpty ? 0 : bestLines.reduce(0.0) { $0 + Double($1.confidence) } / Double(bestLines.count)
let result = OCRResult(version: 1, source: "apple-vision", width: normalized.width, height: normalized.height, rotation: bestRotation, language: "en-US", observations: bestLines, confidence: mean, requiresReview: true)
let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
do { let data = try encoder.encode(result); FileHandle.standardOutput.write(data); FileHandle.standardOutput.write(Data([10])) }
catch { fail("The OCR result could not be encoded.") }
