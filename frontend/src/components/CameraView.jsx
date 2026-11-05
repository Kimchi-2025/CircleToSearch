import React, { useEffect, useRef, useState } from 'react'

const MAX_SIDE = 2400

function canvasToFile(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (!blob) {
        reject(new Error('이미지를 저장하지 못했습니다.'))
      } else {
        resolve(new File([blob], 'circle-search.jpg', { type: 'image/jpeg' }))
      }
    }, 'image/jpeg', 0.88)
  })
}

function drawImage(source, width, height) {
  const scale = Math.min(1, MAX_SIDE / Math.max(width, height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(width * scale)
  canvas.height = Math.round(height * scale)
  canvas.getContext('2d').drawImage(source, 0, 0, canvas.width, canvas.height)
  return canvas
}

export function centerZoomCrop(width, height, aspect, zoom) {
  let cropWidth = width
  let cropHeight = height
  if (width / height > aspect) cropWidth = height * aspect
  else cropHeight = width / aspect
  cropWidth /= zoom
  cropHeight /= zoom
  return [(width - cropWidth) / 2, (height - cropHeight) / 2, cropWidth, cropHeight]
}

export async function prepareUploadedImage(file) {
  if (!file.type.startsWith('image/')) throw new Error('이미지 파일을 선택하세요.')
  const url = URL.createObjectURL(file)
  try {
    const image = new Image()
    image.src = url
    await image.decode()
    if (!image.naturalWidth || !image.naturalHeight) throw new Error('이미지를 읽지 못했습니다.')
    const canvas = drawImage(image, image.naturalWidth, image.naturalHeight)
    return { file: await canvasToFile(canvas), width: canvas.width, height: canvas.height,
      sourceWidth: image.naturalWidth, sourceHeight: image.naturalHeight }
  } finally {
    URL.revokeObjectURL(url)
  }
}

export async function captureCameraFrame(video, zoomOptions) {
  if (!video?.videoWidth || !video?.videoHeight) throw new Error('카메라 영상이 준비되지 않았습니다.')
  if (zoomOptions) {
    const { sourceWidth, sourceHeight, digitalZoom } = zoomOptions
    const scale = Math.min(1, MAX_SIDE / Math.max(sourceWidth, sourceHeight))
    const canvas = document.createElement('canvas')
    canvas.width = Math.round(sourceWidth * scale)
    canvas.height = Math.round(sourceHeight * scale)
    const crop = centerZoomCrop(video.videoWidth, video.videoHeight,
      sourceWidth / sourceHeight, digitalZoom)
    canvas.getContext('2d').drawImage(video, ...crop, 0, 0, canvas.width, canvas.height)
    return { file: await canvasToFile(canvas), width: canvas.width, height: canvas.height,
      sourceWidth, sourceHeight }
  }
  const canvas = drawImage(video, video.videoWidth, video.videoHeight)
  return { file: await canvasToFile(canvas), width: canvas.width, height: canvas.height,
    sourceWidth: video.videoWidth, sourceHeight: video.videoHeight }
}

export default function CameraView({ onImage }) {
  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const pickerRef = useRef(null)
  const [active, setActive] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  function stopCamera() {
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
    setActive(false)
  }

  useEffect(() => () => {
    streamRef.current?.getTracks().forEach((track) => track.stop())
  }, [])

  async function startCamera() {
    setError('')
    if (!navigator.mediaDevices?.getUserMedia) {
      setError('이 브라우저에서는 카메라를 사용할 수 없습니다. HTTPS 또는 localhost에서 열거나 사진을 업로드하세요.')
      return
    }
    setBusy(true)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: { ideal: 'environment' } },
      })
      streamRef.current = stream
      const video = videoRef.current
      if (!video) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      video.srcObject = stream
      await video.play()
      setActive(true)
    } catch (caught) {
      stopCamera()
      setError(caught.name === 'NotAllowedError'
        ? '카메라 권한이 거부되었습니다. 브라우저 권한을 허용하거나 사진을 업로드하세요.'
        : '카메라를 실행하지 못했습니다. 다른 앱의 카메라 사용 여부와 보안 연결을 확인하세요.')
    } finally {
      setBusy(false)
    }
  }

  async function capture() {
    const video = videoRef.current
    if (!video?.videoWidth || !video?.videoHeight) {
      setError('카메라 영상이 준비되지 않았습니다. 잠시 후 다시 시도하세요.')
      return
    }
    setBusy(true)
    try {
      const data = await captureCameraFrame(video)
      stopCamera()
      onImage(data)
    } catch (caught) {
      setError(caught.message)
    } finally {
      setBusy(false)
    }
  }

  async function upload(event) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setError('')
    if (!file.type.startsWith('image/')) {
      setError('이미지 파일을 선택하세요.')
      return
    }
    setBusy(true)
    try {
      const data = await prepareUploadedImage(file)
      stopCamera()
      onImage(data)
    } catch (caught) {
      setError(caught.message === '이미지 파일을 선택하세요.' ? caught.message
        : '이미지를 읽지 못했습니다. 다른 파일을 선택하세요.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card camera-card" aria-label="사진 준비">
      <div className="section-heading"><span className="step">01</span><h2>사진 준비</h2></div>
      <p className="hint">사진을 촬영하거나 저장된 이미지를 선택하세요.</p>
      <video ref={videoRef} className={`camera-video ${active ? '' : 'hidden'}`} playsInline muted autoPlay />
      <div className="button-row">
        {active ? (
          <><button onClick={capture} disabled={busy}>사진 촬영</button><button className="secondary" onClick={stopCamera}>카메라 닫기</button></>
        ) : (
          <button onClick={startCamera} disabled={busy}>카메라 실행</button>
        )}
        <button className="secondary" onClick={() => pickerRef.current?.click()} disabled={busy}>사진 업로드</button>
      </div>
      <input ref={pickerRef} type="file" accept="image/*" onChange={upload} className="visually-hidden" aria-label="이미지 파일 선택" />
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  )
}
