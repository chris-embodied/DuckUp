// macOS JavaScript for Automation. Both arguments are local file paths.
ObjC.import('AppKit');
ObjC.import('CoreImage');
ObjC.import('CoreGraphics');

function run(argv) {
    const raw = $.NSString.stringWithContentsOfFileEncodingError($(argv[0]), $.NSASCIIStringEncoding, null);
    if (!raw) throw new Error('Cannot read owner file');
    const key = ObjC.unwrap(raw);
    if (!/^[0-9a-f]{64}\n$/.test(key)) throw new Error('Invalid owner file');
    const filter = $.CIFilter.filterWithName('CIQRCodeGenerator');
    filter.setValueForKey($('duckbase-owner:v1:' + key.trim()).dataUsingEncoding($.NSASCIIStringEncoding), 'inputMessage');
    filter.setValueForKey($('M'), 'inputCorrectionLevel');
    const qr = filter.outputImage;
    const bounds = qr.extent;
    // Four blank modules around the symbol and integer scaling help phone cameras focus.
    const extent = $.CGRectMake(bounds.origin.x-4, bounds.origin.y-4, bounds.size.width+8, bounds.size.height+8);
    const white = $.CIImage.imageWithColor($.CIColor.whiteColor).imageByCroppingToRect(extent);
    const image = qr.imageByCompositingOverImage(white).imageByApplyingTransform($.CGAffineTransformMakeScale(12, 12));
    const opts = $.NSMutableDictionary.alloc.init;
    opts.setObjectForKey($(true), $.kCIContextUseSoftwareRenderer);
    const context = $.CIContext.contextWithOptions(opts);
    const cg = context.createCGImageFromRect(image, image.extent);
    const pixels = $.NSBitmapImageRep.alloc.initWithCGImage(cg);
    const png = pixels.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $({}));
    if (!png || !png.writeToFileAtomically($(argv[1]), false)) throw new Error('Cannot write QR image');
}
