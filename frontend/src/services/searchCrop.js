// All boxes use the pixels of the same original image sent to FastAPI.
export function paddedCropBox(box, width, height) {
  if (!Array.isArray(box) || box.length !== 4 ||
      !Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 ||
      box.some((value) => !Number.isFinite(value))) return null

  const [left, top, right, bottom] = box
  if (left < 0 || top < 0 || right >= width || bottom >= height || left >= right || top >= bottom) return null
  const padX = Math.max(8, Math.ceil((right - left + 1) * 0.08))
  const padY = Math.max(8, Math.ceil((bottom - top + 1) * 0.08))
  return [
    Math.max(0, Math.floor(left - padX)),
    Math.max(0, Math.floor(top - padY)),
    Math.min(width - 1, Math.ceil(right + padX)),
    Math.min(height - 1, Math.ceil(bottom + padY)),
  ]
}

export async function createSearchImageCrop(image, box) {
  const cropBoundingBox = paddedCropBox(box, image.width, image.height)
  if (!cropBoundingBox) return null
  const bitmap = await createImageBitmap(image.file)
  try {
    if (bitmap.width !== image.width || bitmap.height !== image.height) {
      throw new Error('검색용 crop의 원본 이미지 크기가 일치하지 않습니다.')
    }
    const [left, top, right, bottom] = cropBoundingBox
    const width = right - left + 1
    const height = bottom - top + 1
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const context = canvas.getContext('2d')
    if (!context) throw new Error('검색용 crop을 생성할 수 없습니다.')
    context.drawImage(bitmap, left, top, width, height, 0, 0, width, height)
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.9))
    if (!blob) throw new Error('검색용 crop을 저장할 수 없습니다.')
    return {
      file: new File([blob], 'search-crop.jpg', { type: 'image/jpeg' }),
      crop_bounding_box: cropBoundingBox,
    }
  } finally {
    bitmap.close()
  }
}
