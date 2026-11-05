import React, { useEffect, useRef, useState } from 'react'

export default function MobileTryOn({ product, searchImage, personImage, result, loading, error,
  photoError, photoLoading, onUploadPerson, onRestorePerson, onGenerate, onBack }) {
  const pickerRef = useRef(null)
  const [compareView, setCompareView] = useState('original')
  const [downloadError, setDownloadError] = useState('')
  const [downloading, setDownloading] = useState(false)
  const person = personImage || searchImage
  const hasResult = typeof result?.image_data_url === 'string' && Boolean(result.image_data_url)
  const showingResult = hasResult && compareView === 'result'

  useEffect(() => {
    if (hasResult) setCompareView('result')
  }, [hasResult, result?.image_data_url])

  async function saveResultImage() {
    if (downloading) return
    if (!hasResult) {
      setDownloadError('저장할 가상 착용 결과 이미지가 없습니다.')
      return
    }
    setDownloadError('')
    setDownloading(true)
    try {
      const source = result.image_data_url
      let blob
      if (source.startsWith('data:')) {
        const separator = source.indexOf(',')
        const header = source.slice(5, separator)
        if (separator < 0 || !header.endsWith(';base64')) throw new Error('이미지 데이터가 올바르지 않습니다.')
        const binary = atob(source.slice(separator + 1))
        const bytes = new Uint8Array(binary.length)
        for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
        blob = new Blob([bytes], { type: header.slice(0, -7) })
      } else {
        const response = await fetch(source)
        if (!response.ok) throw new Error('이미지를 읽지 못했습니다.')
        blob = await response.blob()
      }
      const extension = {
        'image/jpeg': 'jpg', 'image/jpg': 'jpg', 'image/png': 'png',
        'image/webp': 'webp', 'image/avif': 'avif', 'image/gif': 'gif',
      }[blob.type.split(';')[0].toLowerCase()]
      if (!blob.size || !extension) throw new Error('저장 가능한 이미지 형식이 아닙니다.')
      const now = new Date()
      const stamp = [now.getFullYear(), now.getMonth() + 1, now.getDate(),
        now.getHours(), now.getMinutes(), now.getSeconds()]
        .map((part, index) => index === 0 ? String(part) : String(part).padStart(2, '0'))
      const objectUrl = URL.createObjectURL(blob)
      try {
        const link = document.createElement('a')
        link.href = objectUrl
        link.download = `onfit-tryon-${stamp.slice(0, 3).join('')}-${stamp.slice(3).join('')}.${extension}`
        document.body.appendChild(link)
        link.click()
        link.remove()
      } finally {
        window.setTimeout(() => URL.revokeObjectURL(objectUrl), 10000)
      }
    } catch {
      setDownloadError('이미지 다운로드를 시작하지 못했습니다. 다시 시도해 주세요.')
    } finally {
      setDownloading(false)
    }
  }

  return <main className={`mobile-app mobile-tryon-screen${hasResult ? ' has-result' : ''}`}>
    <div className="mobile-tryon-viewport">
      <div className="mobile-tryon-stage">
        <img
          src={showingResult ? result.image_data_url : person?.url}
          alt={showingResult ? '가상 착용 결과' : '가상 착용 전 인물 사진'} />
      </div>
    </div>
    <header className="mobile-tryon-header">
      <button type="button" onClick={onBack} aria-label="추천 상품으로 돌아가기">
        <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
          strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="m15 5-7 7 7 7" />
        </svg>
      </button>
      <h1><strong>Onfit</strong><span> Virtual Try-On</span></h1>
    </header>
    <aside className="mobile-tryon-product-panel" aria-label="선택한 상품과 이미지 보기">
      <div className="mobile-tryon-product">
        {typeof product.image_url === 'string' && product.image_url.startsWith('https://') &&
          <img src={product.image_url} alt="선택한 상품" />}
        <div className="mobile-tryon-product-copy">
          {product.brand && <small>{product.brand}</small>}
          <strong>{product.product_name}</strong>
          <small>{personImage ? '업로드 사진' : '원본 사진'}</small>
        </div>
      </div>
      {hasResult && <div className={`mobile-tryon-compare${showingResult ? ' show-result' : ''}`} aria-label="이미지 보기">
        <span className="mobile-tryon-active-segment" aria-hidden="true" />
        <button type="button" className={compareView === 'original' ? 'is-active' : ''}
          onClick={() => setCompareView('original')} aria-pressed={compareView === 'original'}>원본 사진</button>
        <button type="button" className={showingResult ? 'is-active' : ''}
          onClick={() => setCompareView('result')} aria-pressed={showingResult}>가상 착용 결과</button>
      </div>}
    </aside>
    {loading && <div className="mobile-center-guide is-loading mobile-tryon-loading" role="status">
      <strong>{'가상 착용 이미지를\n생성하고 있습니다...'}</strong>
    </div>}
    <footer className="mobile-tryon-actions">
      {photoError && <p className="mobile-error" role="alert">{photoError}</p>}
      {error && <p className="mobile-error" role="alert">{error}</p>}
      {downloadError && <p className="mobile-error" role="alert">{downloadError}</p>}
      {hasResult ? <>
        <div className="mobile-tryon-result-actions">
          <button type="button" className="mobile-tryon-return" onClick={onBack}>
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="m15 5-7 7 7 7" />
            </svg>
            <span>이전 화면으로</span>
          </button>
          <button type="button" className="mobile-tryon-save" onClick={saveResultImage}
            disabled={downloading}>
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 3v11m-4-4 4 4 4-4M4 17v3h16v-3" />
            </svg>
            <span>{downloading ? '이미지 준비 중…' : '가상 이미지 저장'}</span>
          </button>
        </div>
      </> : <>
        <div className="mobile-tryon-action-row">
          <button type="button" className="mobile-tryon-secondary" onClick={() => pickerRef.current?.click()}
            disabled={loading || photoLoading}>다른 사진 업로드</button>
          <button type="button" className="mobile-tryon-secondary" onClick={onRestorePerson}
            disabled={!personImage || loading || photoLoading}>원본 사진 되돌리기</button>
        </div>
        <button type="button" className="mobile-tryon-generate" onClick={onGenerate}
          disabled={loading || photoLoading || !person?.file || !product?.image_url}>
          {loading ? '생성 중…' : error ? '다시 시도' : '가상 착용 생성'}
        </button>
      </>}
    </footer>
    <input ref={pickerRef} type="file" className="mobile-file-input"
      accept="image/*"
      aria-label="가상 착용용 인물 사진 선택" onChange={onUploadPerson} />
  </main>
}
