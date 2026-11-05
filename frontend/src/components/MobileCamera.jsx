import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { captureCameraFrame, centerZoomCrop, prepareUploadedImage } from './CameraView.jsx'
import CircleSelector from './CircleSelector.jsx'
import MobileTryOn from './MobileTryOn.jsx'
import './MobileCamera.css'

function cameraError(error) {
  if (error?.name === 'NotAllowedError' || error?.name === 'PermissionDeniedError') {
    return '카메라 권한이 거부되었습니다. 권한을 허용하거나 사진을 업로드하세요.'
  }
  if (error?.name === 'NotFoundError' || error?.name === 'OverconstrainedError') {
    return '사용 가능한 카메라가 없습니다. 사진을 업로드하세요.'
  }
  return '카메라를 실행하지 못했습니다. HTTPS 연결과 다른 앱의 카메라 사용 여부를 확인하거나 사진을 업로드하세요.'
}

function musinsaProductUrl(value) {
  if (typeof value !== 'string') return null
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && ['www.musinsa.com', 'musinsa.com'].includes(url.hostname)
      && /^\/(?:products|app\/goods)\/\d+$/.test(url.pathname) ? url.href : null
  } catch {
    return null
  }
}

function currentTrackZoom(track, fallback) {
  try {
    const value = Number(track?.getSettings?.().zoom)
    return Number.isFinite(value) && value > 0 ? value : fallback
  } catch {
    return fallback
  }
}

export default function MobileCamera({ image, selection, result, analyzing, analysisError,
  searching, recommendations, searchError, onRetryRecommendations,
  onImage, onCancel, onSelectionChange, onAnalyze, onSelectTryOn,
  selectedTryOnProduct, canTryOn, similarityLabels,
  tryOnPerson, tryOnResult, tryOnError, tryOnLoading, tryOnPhotoError, tryOnPhotoLoading,
  lastTryOn,
  onUploadTryOnPerson, onRestoreTryOnPerson, onCreateTryOn, onLeaveTryOn }) {
  const [mode, setMode] = useState(image ? 'photo' : 'start')
  const [busy, setBusy] = useState(false)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState('')
  const [infoOpen, setInfoOpen] = useState(false)
  const [drawingReset, setDrawingReset] = useState(0)
  const [cardsVisible, setCardsVisible] = useState(true)
  const [cardIndex, setCardIndex] = useState(0)
  const [selectionLocked, setSelectionLocked] = useState(Boolean(result))
  const [hasStartedDrawing, setHasStartedDrawing] = useState(false)
  const [facingMode, setFacingMode] = useState('environment')
  const [zoom, setZoom] = useState(1)
  const [digitalZoom, setDigitalZoom] = useState(1)
  const [zoomApplying, setZoomApplying] = useState(false)
  const [zoomPreview, setZoomPreview] = useState(false)
  const [mobileTryOnOpen, setMobileTryOnOpen] = useState(false)
  const [showLastTryOn, setShowLastTryOn] = useState(false)
  const videoRef = useRef(null)
  const zoomCanvasRef = useRef(null)
  const streamRef = useRef(null)
  const baseFrameRef = useRef(null)
  const zoomInfoRef = useRef(null)
  const zoomTargetRef = useRef(1)
  const digitalZoomRef = useRef(1)
  const zoomSessionRef = useRef(0)
  const zoomWorkingRef = useRef(false)
  const pickerRef = useRef(null)
  const requestRef = useRef(0)
  const cameraBusyRef = useRef(false)
  const historyEntryRef = useRef(false)
  const tryOnHistoryRef = useRef(false)
  const tryOnBackPendingRef = useRef(false)
  const cardListRef = useRef(null)
  const indicatorTrackRef = useRef(null)
  const indicatorThumbRef = useRef(null)
  const onCancelRef = useRef(onCancel)
  const onLeaveTryOnRef = useRef(onLeaveTryOn)
  onCancelRef.current = onCancel
  onLeaveTryOnRef.current = onLeaveTryOn

  useEffect(() => setInfoOpen(Boolean(result)), [result])
  useEffect(() => setHasStartedDrawing(false), [image?.url])
  useEffect(() => setShowLastTryOn(false), [image?.url])

  useEffect(() => {
    setCardIndex(0)
    setCardsVisible(true)
    if (cardListRef.current) cardListRef.current.scrollTop = 0
    if (Array.isArray(recommendations?.recommendations) && recommendations.recommendations.length) {
      setInfoOpen(false)
    }
  }, [recommendations])

  function stopStream() {
    zoomSessionRef.current += 1
    zoomWorkingRef.current = false
    zoomInfoRef.current = null
    baseFrameRef.current = null
    digitalZoomRef.current = 1
    zoomTargetRef.current = 1
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    if (videoRef.current) videoRef.current.srcObject = null
  }

  function configureZoom(stream) {
    const track = stream.getVideoTracks()[0]
    let capabilities = {}
    let settings = {}
    try {
      capabilities = track?.getCapabilities?.() || {}
      settings = track?.getSettings?.() || {}
    } catch { /* Digital zoom remains available without camera capabilities. */ }
    const baseZoom = Number(settings.zoom) || Number(capabilities.zoom?.min) || 1
    const maximum = Number(capabilities.zoom?.max)
    zoomInfoRef.current = { baseZoom, maxZoom: Number.isFinite(maximum) ? maximum : baseZoom }
    baseFrameRef.current = { width: videoRef.current.videoWidth || settings.width,
      height: videoRef.current.videoHeight || settings.height }
    zoomTargetRef.current = 1
    digitalZoomRef.current = 1
    setZoom(1)
    setDigitalZoom(1)
    setZoomPreview(false)
    setZoomApplying(false)
  }

  function changeZoom(event) {
    if (!ready || !streamRef.current) return
    if (!baseFrameRef.current?.width || !baseFrameRef.current.height) {
      setError('이 기기에서는 영상 크기를 확인할 수 없어 확대를 사용할 수 없습니다.')
      return
    }
    const next = Number(event.target.value)
    zoomTargetRef.current = next
    setZoom(next)
    if (zoomWorkingRef.current) return
    const session = zoomSessionRef.current
    const track = streamRef.current.getVideoTracks()[0]
    zoomWorkingRef.current = true
    setZoomApplying(true)
    void (async () => {
      try {
        while (session === zoomSessionRef.current && track?.readyState === 'live') {
          const target = zoomTargetRef.current
          const info = zoomInfoRef.current
          if (!info) break
          let nativeZoom = info.baseZoom
          let failed = false
          if (info.maxZoom > info.baseZoom && typeof track.applyConstraints === 'function') {
            const desired = Math.min(info.maxZoom, info.baseZoom * target)
            try {
              await track.applyConstraints({ advanced: [{ zoom: desired }] })
              nativeZoom = currentTrackZoom(track, desired)
            } catch {
              failed = true
              nativeZoom = currentTrackZoom(track, info.baseZoom)
            }
          }
          if (session !== zoomSessionRef.current || target !== zoomTargetRef.current) continue
          const nativeFactor = Math.max(1, nativeZoom / info.baseZoom)
          if (nativeFactor > target + .05) {
            zoomTargetRef.current = Math.min(3, Math.round(nativeFactor * 10) / 10)
            setZoom(zoomTargetRef.current)
          }
          const residual = Math.max(1, zoomTargetRef.current / nativeFactor)
          digitalZoomRef.current = residual
          setDigitalZoom(residual)
          const base = baseFrameRef.current
          const video = videoRef.current
          setZoomPreview(zoomTargetRef.current > 1.001 || Boolean(base && video &&
            (video.videoWidth !== base.width || video.videoHeight !== base.height)))
          if (failed) setError('하드웨어 줌을 적용하지 못해 디지털 줌으로 전환했습니다.')
          else setError('')
          break
        }
      } finally {
        if (session === zoomSessionRef.current) {
          zoomWorkingRef.current = false
          setZoomApplying(false)
        }
      }
    })()
  }

  useEffect(() => {
    if (mode !== 'camera' || !ready || !zoomPreview) return
    let frame
    function renderZoomPreview() {
      const video = videoRef.current
      const canvas = zoomCanvasRef.current
      const base = baseFrameRef.current
      if (video?.videoWidth && video.videoHeight && canvas && base?.width && base.height) {
        const scale = Math.min(1, 720 / Math.max(base.width, base.height))
        const width = Math.round(base.width * scale)
        const height = Math.round(base.height * scale)
        if (canvas.width !== width || canvas.height !== height) {
          canvas.width = width
          canvas.height = height
        }
        const crop = centerZoomCrop(video.videoWidth, video.videoHeight,
          base.width / base.height, digitalZoomRef.current)
        canvas.getContext('2d').drawImage(video, ...crop, 0, 0, width, height)
      }
      frame = requestAnimationFrame(renderZoomPreview)
    }
    frame = requestAnimationFrame(renderZoomPreview)
    return () => cancelAnimationFrame(frame)
  }, [mode, ready, zoomPreview, digitalZoom])

  function enterSession() {
    if (historyEntryRef.current) return
    window.history.pushState({ ...(window.history.state || {}), circleToSearchMobile: true }, '')
    historyEntryRef.current = true
  }

  function leaveSession() {
    requestRef.current += 1
    cameraBusyRef.current = false
    stopStream()
    setMode('start')
    setBusy(false)
    setReady(false)
    setError('')
    setSelectionLocked(false)
    setFacingMode('environment')
    setZoom(1)
    setZoomPreview(false)
    onCancelRef.current()
    if (historyEntryRef.current) {
      historyEntryRef.current = false
      window.history.back()
    }
  }

  useEffect(() => {
    function handleBack() {
      if (tryOnHistoryRef.current) {
        tryOnHistoryRef.current = false
        tryOnBackPendingRef.current = false
        onLeaveTryOnRef.current?.()
        setMobileTryOnOpen(false)
        setShowLastTryOn(false)
        setCardsVisible(true)
        setInfoOpen(false)
        return
      }
      if (!historyEntryRef.current) return
      historyEntryRef.current = false
      requestRef.current += 1
      cameraBusyRef.current = false
      stopStream()
      setMode('start')
      setBusy(false)
      setReady(false)
      setError('')
      setSelectionLocked(false)
      setFacingMode('environment')
      setZoom(1)
      setZoomPreview(false)
      onCancelRef.current()
    }
    window.addEventListener('popstate', handleBack)
    return () => {
      window.removeEventListener('popstate', handleBack)
      requestRef.current += 1
      stopStream()
    }
  }, [])

  async function startCamera() {
    if (cameraBusyRef.current || streamRef.current) return
    setError('')
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      setError('카메라는 HTTPS 또는 localhost에서 사용할 수 있습니다. 사진 업로드를 이용하세요.')
      return
    }
    enterSession()
    const request = ++requestRef.current
    cameraBusyRef.current = true
    setBusy(true)
    setReady(false)
    setZoom(1)
    setZoomPreview(false)
    setMode('camera')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: { ideal: facingMode } },
      })
      if (request !== requestRef.current) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      streamRef.current = stream
      const video = videoRef.current
      if (!video) throw new Error('video unavailable')
      video.srcObject = stream
      await video.play()
      if (request !== requestRef.current) return
      configureZoom(stream)
      setReady(true)
    } catch (caught) {
      if (request !== requestRef.current) return
      stopStream()
      setMode('start')
      setError(cameraError(caught))
      if (historyEntryRef.current) {
        historyEntryRef.current = false
        window.history.back()
      }
    } finally {
      if (request === requestRef.current) {
        cameraBusyRef.current = false
        setBusy(false)
      }
    }
  }

  async function switchCamera() {
    if (mode !== 'camera' || cameraBusyRef.current || zoomWorkingRef.current || !ready) return
    const previous = facingMode
    const next = previous === 'environment' ? 'user' : 'environment'
    const request = ++requestRef.current
    cameraBusyRef.current = true
    setBusy(true)
    setReady(false)
    setError('')
    stopStream()
    setZoom(1)
    setZoomPreview(false)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false, video: { facingMode: { exact: next } },
      })
      if (request !== requestRef.current) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      streamRef.current = stream
      if (!videoRef.current) throw new Error('video unavailable')
      videoRef.current.srcObject = stream
      await videoRef.current.play()
      if (request !== requestRef.current) return
      configureZoom(stream)
      setFacingMode(next)
      setReady(true)
    } catch (caught) {
      if (request !== requestRef.current) return
      stopStream()
      try {
        const restored = await navigator.mediaDevices.getUserMedia({
          audio: false, video: { facingMode: { ideal: previous } },
        })
        if (request !== requestRef.current) {
          restored.getTracks().forEach((track) => track.stop())
          return
        }
        streamRef.current = restored
        if (!videoRef.current) throw new Error('video unavailable')
        videoRef.current.srcObject = restored
        await videoRef.current.play()
        if (request === requestRef.current) {
          configureZoom(restored)
          setReady(true)
          setError('카메라를 전환할 수 없어 이전 카메라로 돌아왔습니다.')
        }
      } catch {
        if (request === requestRef.current) {
          stopStream()
          setError('카메라 전환에 실패했습니다. 카메라를 종료하고 다시 실행하거나 사진을 업로드해 주세요.')
        }
      }
    } finally {
      if (request === requestRef.current) {
        cameraBusyRef.current = false
        setBusy(false)
      }
    }
  }

  async function capture() {
    if (busy || !ready || zoomWorkingRef.current) return
    const request = requestRef.current
    setBusy(true)
    setError('')
    try {
      const base = baseFrameRef.current
      const video = videoRef.current
      const needsZoomFrame = base?.width && base.height &&
        (zoomTargetRef.current !== 1 || video.videoWidth !== base.width || video.videoHeight !== base.height)
      const data = await captureCameraFrame(video, needsZoomFrame
        ? { sourceWidth: base.width, sourceHeight: base.height, digitalZoom: digitalZoomRef.current }
        : undefined)
      if (request !== requestRef.current) return
      stopStream()
      setReady(false)
      setSelectionLocked(false)
      onImage({ ...data, source: 'camera' })
      setMode('photo')
    } catch (caught) {
      if (request === requestRef.current) setError(caught.message)
    } finally {
      if (request === requestRef.current) setBusy(false)
    }
  }

  async function upload(event) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file || busy) return
    const request = ++requestRef.current
    setBusy(true)
    setError('')
    try {
      const data = await prepareUploadedImage(file)
      if (request !== requestRef.current) return
      enterSession()
      setSelectionLocked(false)
      onImage({ ...data, source: 'upload' })
      setMode('photo')
    } catch (caught) {
      if (request === requestRef.current) setError(caught.message === '이미지 파일을 선택하세요.'
        ? caught.message : '이미지를 읽지 못했습니다. 다른 사진을 선택하세요.')
    } finally {
      if (request === requestRef.current) setBusy(false)
    }
  }

  function retake() {
    onCancelRef.current()
    startCamera()
  }

  function redraw() {
    setSelectionLocked(false)
    setHasStartedDrawing(false)
    setDrawingReset((value) => value + 1)
    setInfoOpen(false)
    onSelectionChange(null)
  }

  function beginSearch() {
    if (analyzing || result) return
    setSelectionLocked(true)
    onAnalyze()
  }

  function hidePhotoOverlays() {
    setInfoOpen(false)
    setCardsVisible(false)
  }

  function enterTryOn(product) {
    if (!canTryOn(product)) return
    onSelectTryOn(product)
    setShowLastTryOn(false)
    setInfoOpen(false)
    setCardsVisible(false)
    setMobileTryOnOpen(true)
    enterSession()
    window.history.pushState({ ...(window.history.state || {}), circleToSearchTryOn: true }, '')
    tryOnHistoryRef.current = true
  }

  function reopenLastTryOn() {
    if (!lastTryOn?.result?.image_data_url) return
    setShowLastTryOn(true)
    setMobileTryOnOpen(true)
    enterSession()
    window.history.pushState({ ...(window.history.state || {}), circleToSearchTryOn: true }, '')
    tryOnHistoryRef.current = true
  }

  function returnToRecommendations() {
    if (tryOnHistoryRef.current) {
      if (tryOnBackPendingRef.current) return
      tryOnBackPendingRef.current = true
      window.history.back()
      return
    }
    onLeaveTryOnRef.current?.()
    setMobileTryOnOpen(false)
    setShowLastTryOn(false)
    setCardsVisible(true)
    setInfoOpen(false)
  }

  function handleCardScroll(event) {
    const list = event.currentTarget
    const maxScroll = Math.max(0, list.scrollHeight - list.clientHeight)
    const progress = maxScroll ? Math.min(1, Math.max(0, list.scrollTop / maxScroll)) : 0
    const track = indicatorTrackRef.current
    const thumb = indicatorThumbRef.current
    if (track && thumb) {
      thumb.style.transform = `translateY(${progress * Math.max(0, track.clientHeight - thumb.clientHeight)}px)`
    }
    const nearest = list.clientHeight ? Math.round(list.scrollTop / list.clientHeight) : 0
    setCardIndex(Math.max(0, Math.min(recommendedProducts.length - 1, nearest)))
  }

  const product = result?.selected_object
  const keywords = result?.search_keywords
  const features = [product?.dominant_color,
    ...(Array.isArray(product?.secondary_colors) ? product.secondary_colors : []),
    product?.pattern, product?.visible_text_or_logo, product?.material_appearance,
    product?.fit_or_shape].filter(Boolean).join(' · ')
  const recommendedProducts = Array.isArray(recommendations?.recommendations)
    ? recommendations.recommendations.filter((item) => item && typeof item === 'object' && !Array.isArray(item))
    : []
  const currentIndex = Math.min(cardIndex, Math.max(0, recommendedProducts.length - 1))
  const currentProduct = recommendedProducts[currentIndex]
  const infoVisible = mode === 'photo' && Boolean(result) && infoOpen
  const cardVisible = mode === 'photo' && Boolean(currentProduct) && cardsVisible && !infoVisible
  const centerGuide = analyzing
    ? { title: '상품을 분석하고\n있습니다...', loading: true }
    : searching
      ? { title: '무신사에서 유사 상품을\n찾고 있습니다...', loading: true }
      : mode === 'photo' && image && !selection && !hasStartedDrawing && !result
        ? { title: '찾고 싶은 상품에\n동그라미를 그려 주세요' }
        : null

  useLayoutEffect(() => {
    if (!cardVisible || mobileTryOnOpen || !cardListRef.current) return
    const list = cardListRef.current
    list.scrollTop = currentIndex * list.clientHeight
    handleCardScroll({ currentTarget: list })
  }, [cardVisible, mobileTryOnOpen, recommendations])

  const displayedTryOn = showLastTryOn ? lastTryOn : null
  if (mobileTryOnOpen && (displayedTryOn?.product || selectedTryOnProduct) && image) return <MobileTryOn
    product={displayedTryOn?.product || selectedTryOnProduct} searchImage={image}
    personImage={displayedTryOn?.personImage || tryOnPerson}
    result={displayedTryOn?.result || tryOnResult} loading={!displayedTryOn && tryOnLoading}
    error={displayedTryOn ? '' : tryOnError}
    photoError={tryOnPhotoError} photoLoading={tryOnPhotoLoading}
    onUploadPerson={onUploadTryOnPerson} onRestorePerson={onRestoreTryOnPerson}
    onGenerate={onCreateTryOn} onBack={returnToRecommendations} />

  return <main className="mobile-app" onPointerDownCapture={(event) => {
    if (mode === 'photo' && !selectionLocked && !analyzing && !result &&
        event.target?.classList?.contains('drawing-canvas') &&
        (event.pointerType !== 'mouse' || event.button === 0)) setHasStartedDrawing(true)
  }}>
    <video ref={videoRef} className={`mobile-live-video ${mode === 'camera' ? '' : 'is-hidden'}`}
      autoPlay muted playsInline aria-label="실시간 카메라 미리보기" />
    <canvas ref={zoomCanvasRef} className={`mobile-zoom-preview${mode === 'camera' && zoomPreview ? '' : ' is-hidden'}`}
      aria-hidden="true" />
    {mode === 'photo' && image && <CircleSelector key={`${image.url}-${drawingReset}`}
      image={image} selection={selection} onSelectionChange={onSelectionChange}
      visionGeometry={result?.geometry} mobile hideSelection={Boolean(result)}
      locked={selectionLocked || analyzing || Boolean(result) || searching || Boolean(recommendations)}
      onLockedPointerDown={hidePhotoOverlays} />}
    {mode === 'photo' && centerGuide && <div className={`mobile-center-guide${centerGuide.loading ? ' is-loading' : ''}${searching && !analyzing ? ' is-searching' : ''}`}
      role={centerGuide.loading ? 'status' : undefined}>
      <strong>{centerGuide.title}</strong>
    </div>}

    {mode === 'start' ? <div className="mobile-start">
      <h1>Onfit Virtual Try-On</h1>
      <div className="mobile-start-actions">
        <button type="button" className="mobile-primary" onClick={() => pickerRef.current?.click()}
          disabled={busy}>사진 업로드</button>
        <button type="button" className="mobile-secondary" onClick={startCamera}
          disabled={busy}>카메라 실행</button>
      </div>
      {error && <p className="mobile-error" role="alert">{error}</p>}
    </div> : <>
      <header className={`mobile-top-overlay${mode === 'camera' ? ' mobile-camera-header' : ''}${mode === 'photo' && lastTryOn?.result?.image_data_url ? ' has-tryon-return' : ''}`}>
        <h1><strong>Onfit</strong><span> Virtual Try-On</span></h1>
        {mode === 'photo' && lastTryOn?.result?.image_data_url && <button type="button"
          className="mobile-tryon-reopen" onClick={reopenLastTryOn}
          aria-label="마지막 가상 착용 결과 보기" title="가상 착용 결과 보기">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
            strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="m9 5 7 7-7 7" />
          </svg>
        </button>}
        {mode === 'photo' && result && <div className="mobile-header-toggles">
          <button type="button" className={`mobile-info-toggle${infoVisible ? ' is-active' : ''}`}
            onClick={() => setInfoOpen((open) => !open)}
            aria-label={infoVisible ? '상품 분석 정보 숨기기' : '상품 분석 정보 표시'} aria-pressed={infoVisible}>
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
              <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" strokeWidth="2" />
              <circle cx="12" cy="7" r="1.2" fill="currentColor" />
              <path d="M12 11v7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </button>
          {recommendedProducts.length > 0 && <button type="button" className={`mobile-products-toggle${cardVisible ? ' is-active' : ''}`}
            onClick={() => {
              if (cardVisible) setCardsVisible(false)
              else {
                setCardsVisible(true)
                setInfoOpen(false)
              }
            }}
            aria-label={cardVisible ? '추천 상품 숨기기' : '추천 상품 표시'} aria-pressed={cardVisible}>
            <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
              <path d="M4 8h16v13H4zM9 8V6a3 3 0 0 1 6 0v2" fill="none"
                stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
            </svg>
          </button>}
        </div>}
        {mode === 'camera' && <button type="button" className="mobile-close" onClick={leaveSession}
          aria-label="카메라 종료">×</button>}
        {mode === 'camera' && <button type="button" className="mobile-camera-switch" onClick={switchCamera}
          disabled={busy || !ready || zoomApplying} aria-label="전면 후면 카메라 전환" title="카메라 전환">
          <svg viewBox="0 0 32 32" width="28" height="28" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M8 12h3l1.5-2h7L21 12h3a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2v-6a2 2 0 0 1 2-2Z" />
            <circle cx="16" cy="17" r="2.6" />
            <path d="M5 10A12 12 0 0 1 25 7m0 0-1-4m1 4-4-.5" />
            <path d="M27 24A12 12 0 0 1 7 27m0 0 1 3m-1-3 4 .5" />
          </svg>
        </button>}
      </header>
      {mode === 'camera' && <div className="mobile-camera-zoom" aria-label="카메라 확대 조절">
        <span>3.0x</span>
        <div className="mobile-camera-zoom-track">
          <input type="range" min="1" max="3" step="0.1" value={zoom}
            onChange={changeZoom} disabled={!ready || busy || !baseFrameRef.current?.width}
            aria-label="카메라 확대 배율" aria-valuetext={`${zoom.toFixed(1)}배`} />
        </div>
        <span>1.0x</span>
        <output>{zoom.toFixed(1)}x</output>
      </div>}
      {infoVisible && <section className="mobile-analysis-info" aria-label="상품 분석 정보">
        <div className="mobile-info-heading">
          <h2>상품 분석 정보</h2>
        </div>
        {product ? <>
          <p><strong>상품 종류</strong><span>{[product.category, product.product_type].filter(Boolean).join(' / ') || '확인 불가'}</span></p>
          {product.description && <p><strong>상품 설명</strong><span>{product.description}</span></p>}
          {features && <p><strong>색상·디자인</strong><span>{features}</span></p>}
          {keywords?.primary && <p><strong>핵심 검색어</strong><span>{keywords.primary}</span></p>}
        </> : <p>{result.message || '상품을 확인하지 못했습니다.'}</p>}
        <small>무신사에서만 상품을 검색합니다.</small>
      </section>}
      {cardVisible && <aside className="mobile-recommendation-scroll" aria-label="무신사 추천 상품">
        <div className="mobile-recommendation-heading">
          <strong>추천 상품</strong>
          <span>{currentIndex + 1} / {recommendedProducts.length}</span>
        </div>
        <div ref={cardListRef} className="mobile-recommendation-list" onScroll={handleCardScroll}>
          {recommendedProducts.map((item, index) => {
            const itemLink = musinsaProductUrl(item.product_url)
            const imageElement = typeof item.image_url === 'string' && item.image_url.startsWith('https://') &&
              <img src={item.image_url} alt={item.product_name || '무신사 상품'} />
            const nameElement = item.product_name && <strong>{item.product_name}</strong>
            return <article className="mobile-recommendation-card" key={`${item.product_id ?? 'item'}-${index}`}>
              {itemLink ? <a className="mobile-product-link" href={itemLink}
                target="_blank" rel="noopener noreferrer">{imageElement}{nameElement}</a>
                : <div className="mobile-product-link">{imageElement}{nameElement}</div>}
              {item.brand && <small>{item.brand}</small>}
              {Number.isFinite(item.price) &&
                <span className="mobile-product-price">{item.price.toLocaleString('ko-KR')}원</span>}
              <div className="mobile-recommendation-actions">
                <div className="mobile-recommendation-meta">
                  {similarityLabels?.[item.similarity_grade] &&
                    <span className="mobile-product-grade">{similarityLabels[item.similarity_grade]}</span>}
                  <span className="mobile-card-navigation">{index + 1} / {recommendedProducts.length}</span>
                </div>
                <button type="button" className="mobile-tryon-button" onClick={() => enterTryOn(item)}
                  disabled={!canTryOn(item)}>착용하기</button>
                {selectedTryOnProduct?.product_id === item.product_id &&
                  <small className="mobile-tryon-selected">선택한 상품입니다.</small>}
              </div>
            </article>
          })}
        </div>
        <div ref={indicatorTrackRef} className="mobile-scroll-indicator" aria-hidden="true">
          <div ref={indicatorThumbRef} className="mobile-scroll-thumb"
            style={{ height: `${100 / recommendedProducts.length}%` }} />
        </div>
      </aside>}
      {mode === 'camera' && <div className="mobile-bottom-overlay mobile-capture-actions">
        {error && <p className="mobile-error" role="alert">{error}</p>}
        <button type="button" className="mobile-shutter" onClick={capture}
          disabled={busy || !ready || zoomApplying} aria-label="사진 촬영"><span /></button>
      </div>}
      {mode === 'photo' && <div className="mobile-bottom-overlay mobile-photo-actions">
        {error && <p className="mobile-error" role="alert">{error}</p>}
        {analysisError && <p className="mobile-error" role="alert">{analysisError}</p>}
        {searchError && <div className="mobile-search-status" role="alert">
          <span>{searchError}</span>
          <button type="button" className="mobile-secondary" onClick={onRetryRecommendations}
            disabled={searching}>다시 검색</button>
        </div>}
        {Array.isArray(recommendations?.recommendations) && recommendedProducts.length === 0 &&
          <p className="mobile-search-status" role="status">비슷한 무신사 상품을 찾지 못했습니다.</p>}
        {selection && <div className="mobile-action-row">
          <button type="button" className="mobile-primary" onClick={beginSearch}
            disabled={analyzing || Boolean(result) || busy}>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" aria-hidden="true">
              <circle cx="10.5" cy="10.5" r="6.5" /><path d="m15.5 15.5 5 5" />
            </svg>
            {analyzing ? '분석 중…' : '상품 검색'}</button>
          <button type="button" className="mobile-secondary mobile-redraw" onClick={redraw}
            disabled={analyzing || busy}>다시 그리기</button>
        </div>}
        <div className="mobile-action-row mobile-source-actions">
          <button type="button" className={`mobile-secondary ${image?.source === 'camera' ? 'mobile-retake' : 'mobile-reupload'}`}
            onClick={image?.source === 'camera' ? retake : () => { setError(''); pickerRef.current?.click() }}
            disabled={busy || analyzing}>{image?.source === 'camera' ? '다시 촬영' : '다시 업로드'}</button>
          <button type="button" className="mobile-secondary mobile-home" onClick={leaveSession}
            disabled={busy || analyzing}>메인 화면으로</button>
        </div>
      </div>}
    </>}
    <input ref={pickerRef} type="file" accept="image/*" className="mobile-file-input"
      aria-label="사진 선택" onChange={upload} />
  </main>
}
