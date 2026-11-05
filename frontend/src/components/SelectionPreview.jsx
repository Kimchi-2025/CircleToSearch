import React, { useEffect, useRef } from 'react'

export default function SelectionPreview({ image, selection, visionGeometry }) {
  const canvasRef = useRef(null)
  const showVisionFill = !!(visionGeometry?.polygon && !visionGeometry.needs_review)

  useEffect(() => {
    const canvas = canvasRef.current
    const source = new Image()
    source.onload = () => {
      if (!canvas) return
      const scale = Math.min(1, 700 / Math.max(image.width, image.height))
      canvas.width = Math.round(image.width * scale)
      canvas.height = Math.round(image.height * scale)
      const context = canvas.getContext('2d')
      context.scale(scale, scale)
      context.drawImage(source, 0, 0, image.width, image.height)
      if (showVisionFill) {
        context.beginPath()
        visionGeometry.polygon.forEach(([x, y], index) => {
          if (index === 0) context.moveTo(x, y)
          else context.lineTo(x, y)
        })
        context.closePath()
        context.fillStyle = 'rgba(255, 40, 160, 0.35)'
        context.fill()
        return
      }
      context.beginPath()
      context.rect(0, 0, image.width, image.height)
      selection.polygon.forEach(([x, y], index) => {
        if (index === 0) context.moveTo(x, y)
        else context.lineTo(x, y)
      })
      context.closePath()
      context.fillStyle = 'rgba(8, 18, 27, 0.64)'
      context.fill('evenodd')
      context.beginPath()
      selection.polygon.forEach(([x, y], index) => {
        if (index === 0) context.moveTo(x, y)
        else context.lineTo(x, y)
      })
      context.closePath()
      context.strokeStyle = '#c9fb62'
      context.lineWidth = 5 / scale
      context.stroke()
      const [left, top, right, bottom] = selection.box
      context.strokeStyle = '#72d8e0'
      context.lineWidth = 2 / scale
      context.strokeRect(left, top, right - left, bottom - top)
      context.beginPath()
      context.arc(selection.point[0], selection.point[1], 5 / scale, 0, Math.PI * 2)
      context.fillStyle = '#72d8e0'
      context.fill()
    }
    source.src = image.url
    return () => { source.onload = null }
  }, [image, selection, visionGeometry, showVisionFill])

  return (
    <section className="card" aria-label="선택 영역 확인">
      <div className="section-heading"><span className="step">03</span><h2>선택 영역 확인</h2></div>
      <p className="hint">{showVisionFill ? '분홍색 영역은 Vision이 추정한 상품 윤곽입니다.' : '선택한 영역을 어둡게 구분한 미리보기입니다. 객체 분할 결과는 아닙니다.'}</p>
      <div className="preview-grid">
        <figure><img src={image.url} alt="전송할 원본 이미지" /><figcaption>원본 이미지</figcaption></figure>
        <figure><canvas ref={canvasRef} role="img" aria-label={showVisionFill ? 'Vision 추정 영역 미리보기' : '선택 경로, 바운딩 박스, 중심점 미리보기'} /><figcaption>{showVisionFill ? 'Vision 추정 영역' : '선택 경로 미리보기'}</figcaption></figure>
      </div>
      <dl className="coordinates">
        <div><dt>원본 크기</dt><dd>{image.width} × {image.height} px</dd></div>
        <div><dt>중심점</dt><dd>[{selection.point.join(', ')}]</dd></div>
        <div><dt>바운딩 박스</dt><dd>[{selection.box.join(', ')}]</dd></div>
        <div><dt>경로 점</dt><dd>{selection.polygon.length}개</dd></div>
      </dl>
    </section>
  )
}
