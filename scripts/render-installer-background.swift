#!/usr/bin/env swift
import AppKit

// Background artwork only. The app and Applications folder are real Finder icons.
// Keep these logical coordinates aligned with scripts/package-installer.py.
let width = 720
let height = 440
let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()

func color(_ hex: UInt32) -> NSColor {
    NSColor(srgbRed: CGFloat((hex >> 16) & 255) / 255,
            green: CGFloat((hex >> 8) & 255) / 255,
            blue: CGFloat(hex & 255) / 255, alpha: 1)
}

func text(_ value: String, y: CGFloat, size: CGFloat, weight: NSFont.Weight, ink: UInt32) {
    let paragraph = NSMutableParagraphStyle()
    paragraph.alignment = .center
    let attributes: [NSAttributedString.Key: Any] = [
        .font: NSFont.systemFont(ofSize: size, weight: weight),
        .foregroundColor: color(ink),
        .paragraphStyle: paragraph
    ]
    (value as NSString).draw(in: NSRect(x: 30, y: y, width: 660, height: size * 1.8),
                            withAttributes: attributes)
}

for scale in [1, 2] {
    guard let canvas = CGContext(data: nil, width: width * scale, height: height * scale,
                                bitsPerComponent: 8, bytesPerRow: 0,
                                space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
        fatalError("Could not allocate installer artwork")
    }
    canvas.translateBy(x: 0, y: CGFloat(height * scale))
    canvas.scaleBy(x: CGFloat(scale), y: -CGFloat(scale))
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(cgContext: canvas, flipped: true)

    color(0xF2F6F1).setFill()
    NSRect(x: 0, y: 0, width: width, height: height).fill()

    text("Drag Fieldwork to Applications", y: 43, size: 29, weight: .semibold, ink: 0x163835)

    // Native icon centres: Fieldwork (170, 200), Applications (550, 200).
    // Labels below the icons are provided by Finder, not duplicated in this art.
    color(0x006D5B).setStroke()
    let arrow = NSBezierPath()
    arrow.lineWidth = 4
    arrow.lineCapStyle = .round
    arrow.lineJoinStyle = .round
    arrow.move(to: NSPoint(x: 307, y: 200))
    arrow.line(to: NSPoint(x: 410, y: 200))
    arrow.move(to: NSPoint(x: 394, y: 184))
    arrow.line(to: NSPoint(x: 410, y: 200))
    arrow.line(to: NSPoint(x: 394, y: 216))
    arrow.stroke()

    text("Then open Fieldwork from Applications.", y: 315, size: 17, weight: .medium, ink: 0x163835)
    text("Updating? Quit Fieldwork first, then replace the existing app.",
         y: 367, size: 12.5, weight: .regular, ink: 0x536D66)
    text("Your documents and templates are kept.",
         y: 387, size: 12.5, weight: .regular, ink: 0x536D66)

    NSGraphicsContext.restoreGraphicsState()
    let bitmap = NSBitmapImageRep(cgImage: canvas.makeImage()!)
    bitmap.size = NSSize(width: width, height: height)
    guard let png = bitmap.representation(using: .png, properties: [:]) else {
        fatalError("Could not encode installer artwork")
    }
    let suffix = scale == 1 ? "" : "@2x"
    let destination = root.appendingPathComponent("desktop/installer-background\(suffix).png")
    try png.write(to: destination)
    print(destination.path)
}
