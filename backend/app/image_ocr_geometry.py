"""Non-destructive rotation, margin crop and contrast for phone-captured text."""
from pathlib import Path
from PIL import Image, ImageOps


def prepare_image_geometry(source: Path, destination: Path) -> dict:
    assert source.resolve() != destination.resolve()
    with Image.open(source) as opened:
        image = ImageOps.exif_transpose(opened).convert('RGB')
    image = ImageOps.autocontrast(image, cutoff=0)
    small = ImageOps.grayscale(image)
    small.thumbnail((720,720))
    mask = small.point(lambda value:255 if value < 160 else 0)
    def score(angle):
        rotated = mask.rotate(angle,expand=True,fillcolor=0)
        pixels=rotated.load()
        return sum(sum(pixels[x,y] for x in range(rotated.width))**2 for y in range(rotated.height))
    base=score(0);best=0;best_score=base
    for angle in range(-6,7):
        value=score(angle)
        if value>best_score:best,best_score=angle,value
    if base and best_score>base*1.2:
        image=image.rotate(best,resample=Image.Resampling.BICUBIC,expand=True,fillcolor='white')
    else:best=0
    gray=ImageOps.grayscale(image)
    corners=[gray.getpixel(point) for point in ((0,0),(gray.width-1,0),(0,gray.height-1),(gray.width-1,gray.height-1))]
    cropped=False
    if min(corners)>235:
        bbox=gray.point(lambda value:255 if value<235 else 0).getbbox()
        if bbox:
            left,top,right,bottom=bbox;padding=max(20,min(image.size)//30)
            box=(max(0,left-padding),max(0,top-padding),min(image.width,right+padding),min(image.height,bottom+padding))
            if (right-left)>image.width*.1 and (bottom-top)>image.height*.03:
                image=image.crop(box);cropped=True
    scale=min(2.0,1800/max(1,image.width),2800/max(1,image.height))
    if scale>1.05:image=image.resize((round(image.width*scale),round(image.height*scale)),Image.Resampling.LANCZOS)
    image.thumbnail((2800,2800),Image.Resampling.LANCZOS)
    destination.parent.mkdir(parents=True,exist_ok=True)
    image.save(destination,format='PNG')
    return {'rotation_degrees':best,'text_region_enlarged':scale>1.05,'margin_crop':cropped,'contrast_adjusted':True,'original_preserved':True}
