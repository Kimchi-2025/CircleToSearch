import React, { useEffect, useRef, useState } from 'react'
import CameraView from './components/CameraView.jsx'
import MobileCamera from './components/MobileCamera.jsx'
import CircleSelector from './components/CircleSelector.jsx'
import SelectionPreview from './components/SelectionPreview.jsx'
import SearchDiagnostics from './components/SearchDiagnostics.jsx'
import { sendRecommendations, sendSelection, sendTryOn } from './services/api.js'
import { createSearchImageCrop } from './services/searchCrop.js'
import './App.css'

const similarityLabels = {
  very_similar: '매우 유사',
  similar: '유사',
  somewhat_similar: '일부 유사',
}

const upperGarmentPattern = /상의|아우터|티셔츠|반팔티|긴팔티|셔츠|남방|블라우스|맨투맨|스웨트|후드|니트|가디건|자켓|재킷|점퍼|코트|패딩|블레이저|바람막이|t-?shirt|shirt|blouse|sweatshirt|hoodie|knit|cardigan|outerwear|jacket|coat|puffer|blazer|\btee\b/i
const lowerGarmentPattern = /하의|반바지|바지|팬츠|슬랙스|데님|조거|트라우저|쇼츠|스커트|치마|\b(?:pants?|trousers?|jeans?|shorts?|skirts?|denim|slacks?)\b/i
const shoePattern = /신발|스니커즈|운동화|슈즈|부츠|로퍼|구두|샌들|슬리퍼|힐|\b(?:sneakers?|shoes?|boots?|sandals?|loafers?|heels?|pumps?|slippers?)\b/i
const bagPattern = /가방|백팩|숄더|크로스|토트|핸드백|파우치|\b(?:backpacks?|handbags?|crossbody(?:\s+bags?)?|shoulder\s+bags?|tote(?:\s+bags?)?|bags?)\b/i
const hatPattern = /모자|볼캡|비니|버킷햇|버킷 햇|페도라|\b(?:hats?|caps?|beanies?|bucket\s+hats?|fedoras?)\b/i
const glassesPattern = /안경|선글라스|아이웨어|\b(?:sunglasses|eyeglasses|glasses|eyewear|spectacles)\b/i
const watchPattern = /시계|워치|\b(?:wrist\s*)?watch(?:es)?\b/i
const garmentRegion = (name) => {
  if (typeof name !== 'string') return null
  if (upperGarmentPattern.test(name)) return 'upper'
  if (lowerGarmentPattern.test(name)) return 'lower'
  if (shoePattern.test(name)) return 'shoes'
  if (bagPattern.test(name)) return 'bag'
  if (glassesPattern.test(name)) return 'glasses'
  if (hatPattern.test(name)) return 'hat'
  if (watchPattern.test(name)) return 'watch'
  return null
}
const selectedTryOnRegion = (selected) => {
  const typeRegion = garmentRegion(selected?.product_type)
  const categoryRegion = garmentRegion(selected?.category)
  return typeRegion && categoryRegion && typeRegion !== categoryRegion ? null : typeRegion || categoryRegion
}
const tryOnUnavailableReason = (product, personImage) => {
  if (!personImage?.file) return '원본 인물 사진이 필요합니다.'
  if (!/^\d{1,20}$/.test(String(product?.product_id ?? ''))
    || typeof product?.product_name !== 'string' || !product.product_name.trim()
    || typeof product?.image_url !== 'string' || !product.image_url.trim()) {
    return '상품 ID·이름·이미지 주소가 필요합니다.'
  }
  return ''
}
const canTryOnProduct = (product, personImage) => !tryOnUnavailableReason(product, personImage)

class SearchResultBoundary extends React.Component {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch() {
    this.props.onError()
  }

  render() {
    return this.state.failed
      ? <p className="error" role="alert">검색 결과를 표시하지 못했습니다. 유사 상품 검색을 다시 시도해 주세요.</p>
      : this.props.children
  }
}

export default function App() {
  const [isMobile, setIsMobile] = useState(() => window.matchMedia('(max-width: 768px)').matches)
  const [image, setImage] = useState(null)
  const [selection, setSelection] = useState(null)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')
  const [sending, setSending] = useState(false)
  const [searching, setSearching] = useState(false)
  const [recommendations, setRecommendations] = useState(null)
  const [searchError, setSearchError] = useState('')
  const [searchDiagnostics, setSearchDiagnostics] = useState(null)
  const [searchAttempt, setSearchAttempt] = useState(0)
  const [tryOnProduct, setTryOnProduct] = useState(null)
  const [tryOnResult, setTryOnResult] = useState(null)
  const [lastMobileTryOn, setLastMobileTryOn] = useState(null)
  const [tryOnError, setTryOnError] = useState('')
  const [tryOnLoading, setTryOnLoading] = useState(false)
  const [tryOnPerson, setTryOnPerson] = useState(null)
  const [tryOnPhotoError, setTryOnPhotoError] = useState('')
  const [tryOnPhotoLoading, setTryOnPhotoLoading] = useState(false)
  const tryOnPickerRef = useRef(null)
  const inFlight = useRef(false)
  const searchInFlight = useRef(null)
  const tryOnInFlight = useRef(false)
  const tryOnAbortRef = useRef(null)
  const tryOnVersion = useRef(0)
  const selectionVersion = useRef(0)

  useEffect(() => {
    const query = window.matchMedia('(max-width: 768px)')
    const update = () => setIsMobile(query.matches)
    query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])

  useEffect(() => () => {
    if (image?.url) URL.revokeObjectURL(image.url)
  }, [image])

  useEffect(() => () => {
    if (tryOnPerson?.url) URL.revokeObjectURL(tryOnPerson.url)
  }, [tryOnPerson])

  useEffect(() => () => {
    if (lastMobileTryOn?.personImage?.url) URL.revokeObjectURL(lastMobileTryOn.personImage.url)
  }, [lastMobileTryOn])

  function clearTryOnOutcome() {
    tryOnVersion.current += 1
    setTryOnResult(null)
    setTryOnError('')
  }

  function resetTryOn() {
    clearTryOnOutcome()
    tryOnAbortRef.current?.abort()
    tryOnAbortRef.current = null
    tryOnInFlight.current = false
    setTryOnProduct(null)
    setTryOnLoading(false)
  }

  function leaveMobileTryOn() {
    clearTryOnOutcome()
    tryOnAbortRef.current?.abort()
    tryOnAbortRef.current = null
    tryOnInFlight.current = false
    setTryOnLoading(false)
    setTryOnPhotoError('')
  }

  function acceptImage(data) {
    selectionVersion.current += 1
    resetTryOn()
    setLastMobileTryOn(null)
    setTryOnPerson(null)
    setTryOnPhotoError('')
    setImage({ ...data, url: URL.createObjectURL(data.file) })
    setSelection(null)
    setResult(null)
    setError('')
    setRecommendations(null)
    setSearchError('')
    setSearchDiagnostics(null)
    setSearching(false)
  }

  function resetImage() {
    selectionVersion.current += 1
    resetTryOn()
    setLastMobileTryOn(null)
    setTryOnPerson(null)
    setTryOnPhotoError('')
    setImage(null)
    setSelection(null)
    setResult(null)
    setError('')
    setRecommendations(null)
    setSearchError('')
    setSearchDiagnostics(null)
    setSearching(false)
  }

  function changeSelection(next) {
    selectionVersion.current += 1
    resetTryOn()
    setLastMobileTryOn(null)
    setSelection(next)
    setResult(null)
    setError('')
    setRecommendations(null)
    setSearchError('')
    setSearchDiagnostics(null)
    setSearching(false)
  }

  async function searchSimilar() {
    const submittedVersion = selectionVersion.current
    if (searchInFlight.current === submittedVersion || result?.status !== 'success' || !result.search_image_crop?.file) return
    searchInFlight.current = submittedVersion
    resetTryOn()
    setSearchAttempt((attempt) => attempt + 1)
    setSearching(true)
    setSearchError('')
    setSearchDiagnostics(null)
    try {
      const next = await sendRecommendations(result.search_image_crop.file, result)
      if (submittedVersion === selectionVersion.current) {
        if (next && typeof next === 'object' && !Array.isArray(next)) setRecommendations(next)
        else setSearchError('추천 응답을 읽을 수 없습니다. 다시 시도해 주세요.')
      }
    } catch (caught) {
      if (submittedVersion === selectionVersion.current) {
        setSearchError(caught.message)
        setSearchDiagnostics(caught.diagnostics || null)
      }
    } finally {
      if (searchInFlight.current === submittedVersion) {
        searchInFlight.current = null
        setSearching(false)
      }
    }
  }

  function selectTryOn(product) {
    if (tryOnInFlight.current || !canTryOnProduct(product, image)) return
    clearTryOnOutcome()
    setTryOnProduct(product)
  }

  async function uploadTryOnPerson(event) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file || tryOnLoading || tryOnPhotoLoading) return
    const currentSelectionVersion = selectionVersion.current
    const currentTryOnVersion = tryOnVersion.current
    setTryOnPhotoError('')
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 10 * 1024 * 1024) {
      setTryOnPhotoError('10 MB 이하의 JPG, PNG, WebP 사진을 선택하세요.')
      return
    }
    setTryOnPhotoLoading(true)
    const sourceUrl = URL.createObjectURL(file)
    try {
      const source = new Image()
      source.src = sourceUrl
      await source.decode()
      if (!source.naturalWidth || !source.naturalHeight ||
          source.naturalWidth > 8000 || source.naturalHeight > 8000) {
        throw new Error('이미지 크기를 확인할 수 없습니다.')
      }
      const scale = Math.min(1, 2400 / Math.max(source.naturalWidth, source.naturalHeight))
      const canvas = document.createElement('canvas')
      canvas.width = Math.max(1, Math.round(source.naturalWidth * scale))
      canvas.height = Math.max(1, Math.round(source.naturalHeight * scale))
      canvas.getContext('2d').drawImage(source, 0, 0, canvas.width, canvas.height)
      const blob = await new Promise((resolve, reject) => canvas.toBlob(
        (value) => value ? resolve(value) : reject(new Error('이미지를 저장하지 못했습니다.')),
        'image/jpeg', 0.88))
      if (currentSelectionVersion !== selectionVersion.current || currentTryOnVersion !== tryOnVersion.current) return
      const normalized = new File([blob], 'try-on-person.jpg', { type: 'image/jpeg' })
      clearTryOnOutcome()
      setTryOnPerson({ file: normalized, url: URL.createObjectURL(normalized) })
    } catch {
      setTryOnPhotoError('사진을 읽지 못했습니다. JPG, PNG, WebP 파일을 다시 선택하세요.')
    } finally {
      URL.revokeObjectURL(sourceUrl)
      setTryOnPhotoLoading(false)
    }
  }

  function restoreTryOnPerson() {
    if (!tryOnPerson || tryOnLoading) return
    clearTryOnOutcome()
    setTryOnPerson(null)
    setTryOnPhotoError('')
  }

  async function createTryOn() {
    const person = tryOnPerson || image
    if (!tryOnProduct || !person?.file || !result || tryOnInFlight.current) return
    const currentVersion = tryOnVersion.current
    const controller = new AbortController()
    tryOnInFlight.current = true
    tryOnAbortRef.current = controller
    setTryOnLoading(true)
    setTryOnError('')
    setTryOnResult(null)
    try {
      const box = tryOnPerson ? null : result.geometry?.bounding_box || selection?.box
      const analysis = tryOnPerson
        ? { ...result, selected_object: { ...result.selected_object, description: '' } }
        : result
      const next = await sendTryOn(person.file, tryOnProduct, analysis, box, controller.signal)
      if (currentVersion === tryOnVersion.current) {
        setTryOnResult(next)
        if (isMobile && typeof next?.image_data_url === 'string' && next.image_data_url) {
          setLastMobileTryOn({
            product: tryOnProduct,
            personImage: tryOnPerson
              ? { file: person.file, url: URL.createObjectURL(person.file) }
              : null,
            result: next,
          })
        }
      }
    } catch (caught) {
      if (currentVersion === tryOnVersion.current) setTryOnError(caught.message)
    } finally {
      if (tryOnAbortRef.current === controller) {
        tryOnAbortRef.current = null
        tryOnInFlight.current = false
        if (currentVersion === tryOnVersion.current) setTryOnLoading(false)
      }
    }
  }

  async function submit() {
    if (!image || !selection || result || inFlight.current) return
    inFlight.current = true
    const submittedVersion = selectionVersion.current
    setSending(true)
    setError('')
    setResult(null)
    try {
      const next = await sendSelection(image, selection)
      let searchImageCrop = null
      let cropError = ''
      if (next.status === 'success' && next.geometry?.bounding_box) {
        try {
          searchImageCrop = await createSearchImageCrop(image, next.geometry.bounding_box)
        } catch {
          cropError = '검색용 이미지 crop을 만들지 못했습니다. 사진을 다시 선택해 주세요.'
        }
      }
      if (submittedVersion === selectionVersion.current) {
        setResult({ ...next, search_image_crop: searchImageCrop })
        if (cropError) setError(cropError)
      }
    } catch (caught) {
      if (submittedVersion === selectionVersion.current) setError(caught.message)
    } finally {
      inFlight.current = false
      setSending(false)
    }
  }

  useEffect(() => {
    if (isMobile && result?.status === 'success' && result.search_image_crop?.file &&
        !recommendations && !searching && !searchError) searchSimilar()
  }, [isMobile, result, recommendations, searching, searchError])

  if (isMobile) return <MobileCamera image={image} selection={selection} result={result}
    analyzing={sending} analysisError={error} onImage={acceptImage} onCancel={resetImage}
    onSelectionChange={changeSelection} onAnalyze={submit} searching={searching}
    recommendations={recommendations} searchError={searchError} onRetryRecommendations={searchSimilar}
    onSelectTryOn={selectTryOn} selectedTryOnProduct={tryOnProduct}
    canTryOn={(product) => canTryOnProduct(product, image)} similarityLabels={similarityLabels}
    tryOnPerson={tryOnPerson} tryOnResult={tryOnResult} tryOnError={tryOnError}
    lastTryOn={lastMobileTryOn}
    tryOnLoading={tryOnLoading} tryOnPhotoError={tryOnPhotoError} tryOnPhotoLoading={tryOnPhotoLoading}
    onUploadTryOnPerson={uploadTryOnPerson} onRestoreTryOnPerson={restoreTryOnPerson}
    onCreateTryOn={createTryOn} onLeaveTryOn={leaveMobileTryOn} />

  const hasRecommendationList = Array.isArray(recommendations?.recommendations)
  const recommendedProducts = hasRecommendationList
    ? recommendations.recommendations.filter((product) => product && typeof product === 'object' && !Array.isArray(product))
    : []
  const activeTryOnPerson = tryOnPerson || image
  const tryOnPhotoActions = <div className="tryon-photo-actions">
    <button type="button" className="secondary" onClick={() => tryOnPickerRef.current?.click()}
      disabled={tryOnLoading || tryOnPhotoLoading}>다른 사진 업로드</button>
    <button type="button" className="secondary" onClick={restoreTryOnPerson}
      disabled={!tryOnPerson || tryOnLoading || tryOnPhotoLoading}>원본 사진으로 되돌리기</button>
  </div>

  return (
    <main className="app-shell">
      <header className="hero">
        <div className="eyebrow">ONFIT · 독립 기능 테스트</div>
        <h1>Circle to Search</h1>
        <p>사진 속 패션 상품을 둘러 그리면 상품 정보와 추정 윤곽을 분석합니다.</p>
      </header>

      {!image ? <CameraView onImage={acceptImage} /> : (
        <>
          <div className="toolbar"><button className="text-button" onClick={resetImage}>← 다시 촬영 또는 업로드</button></div>
          <CircleSelector image={image} selection={selection} onSelectionChange={changeSelection}
            visionGeometry={result?.geometry} />
          {selection && <>
            <SelectionPreview image={image} selection={selection} visionGeometry={result?.geometry} />
            <section className="card send-card" aria-label="서버 전송">
              <div className="section-heading"><span className="step">04</span><h2>서버에 전송</h2></div>
              <p className="hint">선택한 이미지와 좌표를 전송해 패션 상품 한 개를 분석합니다.</p>
              <button onClick={submit} disabled={sending || !!result}>{sending ? '분석 중…' : result ? '분석 완료' : '선택 영역 전송'}</button>
              {error && <p className="error" role="alert">{error}</p>}
              {result && <div className="response" role="status">
                <strong>{result.status === 'success' ? '패션 상품 분석 결과' : '분석 결과'} · {result.status}</strong>
                <p>{result.message}</p>
                {result.selected_object && <>
                  <p><b>상품:</b> {[result.selected_object.category, result.selected_object.product_type].filter(Boolean).join(' / ') || '종류 확인 불가'}</p>
                  {result.selected_object.description && <p><b>설명:</b> {result.selected_object.description}</p>}
                  <p><b>색상·디자인:</b> {[result.selected_object.dominant_color,
                    ...result.selected_object.secondary_colors, result.selected_object.pattern,
                    result.selected_object.visible_text_or_logo, result.selected_object.material_appearance,
                    result.selected_object.fit_or_shape, result.selected_object.style].filter(Boolean).join(' · ') || '확인 불가'}</p>
                  {result.search_keywords && <p><b>검색어:</b> {[result.search_keywords.primary,
                    ...result.search_keywords.alternatives, result.search_keywords.broad].join(' / ')}</p>}
                  <p><b>추정 윤곽:</b> {result.geometry?.polygon && !result.geometry?.needs_review
                    ? '사진의 핑크색 영역으로 표시' : '추정 실패/재확인 필요'}</p>
                  <p><b>검색용 crop:</b> {result.search_image_crop
                    ? `원본 사진의 [${result.search_image_crop.crop_bounding_box.join(', ')}] 영역 준비 완료`
                    : '준비되지 않음'}</p>
                </>}
                {result.metadata && <small>모델: {result.metadata.model} · 처리 시간: {(result.metadata.duration_ms / 1000).toFixed(1)}초
                  {' · '}토큰: {result.metadata.usage?.total_tokens ?? '확인 불가'}{result.metadata.cached ? ' · 캐시 사용' : ''}</small>}
              </div>}
            </section>
            {result?.status === 'success' && <section className="card recommendations" aria-label="무신사 유사 상품 추천">
              <div className="section-heading"><span className="step">05</span><h2>무신사 유사 상품 추천</h2></div>
              <p className="hint">분석한 상품의 원본 crop과 검색어로 무신사 상품을 찾습니다.</p>
              <button onClick={searchSimilar} disabled={searching || hasRecommendationList || !result.search_image_crop?.file}>
                {searching ? '상품 검색 및 이미지 비교 중…' : hasRecommendationList ? '검색 완료' : '유사 상품 검색'}
              </button>
              {searchError && <p className="error" role="alert">{searchError}</p>}
              <SearchResultBoundary key={searchAttempt} onError={() => { setRecommendations(null); setSearchDiagnostics(null) }}>
                {(recommendations?.search_diagnostics || searchDiagnostics) &&
                  <SearchDiagnostics data={recommendations?.search_diagnostics || searchDiagnostics} />}
                {recommendations && <>
                <p className="hint" role="status">후보 {Number.isFinite(recommendations.candidate_count) ? recommendations.candidate_count : '—'}개 중 추천 {Number.isFinite(recommendations.recommended_count) ? recommendations.recommended_count : '—'}개</p>
                {!hasRecommendationList && <p className="error" role="alert">추천 상품 목록을 읽을 수 없습니다. 다시 검색해 주세요.</p>}
                {hasRecommendationList && recommendedProducts.length === 0 && <p>비슷한 상품을 찾지 못했습니다.</p>}
                <div className="recommendation-grid">
                  {recommendedProducts.map((product, index) => <article className="recommendation-card" key={product.product_id ?? index}>
                    <a className="recommendation-link" href={typeof product.product_url === 'string' ? product.product_url : undefined} target="_blank" rel="noopener noreferrer">
                    {typeof product.image_url === 'string' && <img src={product.image_url} alt={typeof product.product_name === 'string' ? product.product_name : '상품 이미지'} loading="lazy" />}
                    <div className="recommendation-copy">
                      <small>{typeof product.brand === 'string' && product.brand ? product.brand : '브랜드 정보 없음'}</small>
                      <strong>{typeof product.product_name === 'string' ? product.product_name : '상품명 정보 없음'}</strong>
                      <span>{Number.isFinite(product.price) ? `${product.price.toLocaleString('ko-KR')}원` : '가격 정보 없음'}</span>
                      {similarityLabels[product.similarity_grade] &&
                        <small className="similarity-grade">{similarityLabels[product.similarity_grade]}</small>}
                    </div>
                    </a>
                    <button className="tryon-select" type="button" onClick={() => selectTryOn(product)}
                      disabled={tryOnLoading || !canTryOnProduct(product, image)}
                      title={tryOnUnavailableReason(product, image) || undefined}>
                      착용하기
                    </button>
                    {tryOnUnavailableReason(product, image) &&
                      <small className="hint">{tryOnUnavailableReason(product, image)}</small>}
                  </article>)}
                </div>
              </>}
              </SearchResultBoundary>
            </section>}
            {result?.status === 'success' && hasRecommendationList &&
              <section className="card tryon-section" aria-label="AI 가상 착용">
                <div className="section-heading"><span className="step">06</span><h2>AI 가상 착용 (Virtual Try-On)</h2></div>
                      {tryOnProduct ? <div className="tryon-product">
                        {typeof tryOnProduct.image_url === 'string' && <img src={tryOnProduct.image_url} alt={tryOnProduct.product_name || '선택한 상품'} />}
                        <div><small>{tryOnProduct.brand || '브랜드 정보 없음'}</small><strong>{tryOnProduct.product_name}</strong></div>
                      </div> : <p className="hint">추천 상품 카드에서 착용하기를 선택하세요.</p>}
                      <input ref={tryOnPickerRef} type="file" className="visually-hidden"
                        accept=".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"
                        aria-label="가상 착용용 인물 사진 선택" onChange={uploadTryOnPerson} />
                      {tryOnPhotoError && <p className="error" role="alert">{tryOnPhotoError}</p>}
                      {tryOnProduct && <>
                      <button type="button" onClick={createTryOn} disabled={tryOnLoading || tryOnInFlight.current}>
                        {tryOnLoading ? '가상 착용 이미지 생성 중…' : '가상 착용 생성'}
                      </button>
                      {selectedTryOnRegion(result.selected_object) === 'watch' &&
                        <p className="hint">사진에 손목이 보이지 않으면 시계를 자연스럽게 착용시키기 어렵습니다.</p>}
                      <p className="hint">생성 이미지의 로고·프린트는 원본과 다를 수 있습니다.</p>
                      {tryOnError && <p className="error" role="alert">{tryOnError}</p>}
                      </>}
                      <div className="tryon-images">
                        <figure>
                          <img src={activeTryOnPerson.url} alt="가상 착용 전 인물 사진" />
                          <figcaption>착용 전</figcaption>
                          {tryOnPhotoActions}
                        </figure>
                        {tryOnResult?.image_data_url && <figure><img src={tryOnResult.image_data_url} alt="AI 가상 착용 결과" /><figcaption>가상 착용 결과</figcaption></figure>}
                      </div>
                      {tryOnResult && <small className="tryon-metadata">모델: {tryOnResult.model} · 생성 시간: {(tryOnResult.elapsed_ms / 1000).toFixed(1)}초{Number.isFinite(tryOnResult.usage?.total_tokens) ? ` · 토큰: ${tryOnResult.usage.total_tokens}` : ''}</small>}
              </section>}
          </>}
        </>
      )}
      <footer>분홍색 영역은 Vision 추정치입니다. 상품 추천 결과는 실제 무신사 검색 결과를 바탕으로 표시합니다.</footer>
    </main>
  )
}
