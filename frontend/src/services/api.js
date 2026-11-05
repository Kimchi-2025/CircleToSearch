const API_URL = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '')

export async function sendSelection(image, selection) {
  const form = new FormData()
  form.append('image', image.file, image.file.name)
  form.append('width', String(image.width))
  form.append('height', String(image.height))
  form.append('point', JSON.stringify(selection.point))
  form.append('box', JSON.stringify(selection.box))
  form.append('polygon', JSON.stringify(selection.polygon))

  let response
  try {
    response = await fetch(`${API_URL}/api/circle-search/segment`, {
      method: 'POST',
      body: form,
    })
  } catch {
    throw new Error(`API 요청이 차단되거나 서버에 연결할 수 없습니다. 주소(${API_URL || '현재 사이트'}), 실행 상태와 프록시 설정을 확인하세요.`)
  }

  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = data?.detail
    const message = typeof detail === 'string'
      ? detail
      : Array.isArray(detail)
        ? detail.map((item) => item.msg || String(item)).join('; ')
        : response.statusText || '요청을 처리하지 못했습니다.'
    throw new Error(`HTTP ${response.status}: ${message}`)
  }
  return data
}

export async function sendRecommendations(cropFile, analysis) {
  const form = new FormData()
  form.append('crop', cropFile, cropFile.name)
  form.append('analysis', JSON.stringify({
    selected_object: analysis.selected_object,
    search_keywords: analysis.search_keywords,
  }))
  let response
  try {
    response = await fetch(`${API_URL}/api/circle-search/recommend`, { method: 'POST', body: form })
  } catch {
    throw new Error('무신사 검색 서버에 연결할 수 없습니다. FastAPI 실행 상태를 확인하세요.')
  }
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = data?.detail
    const message = typeof detail === 'object' && !Array.isArray(detail)
      ? detail?.message : typeof detail === 'string' ? detail : response.statusText
    const error = new Error(`HTTP ${response.status}: ${message || '추천 요청을 처리하지 못했습니다.'}`)
    error.diagnostics = detail?.search_diagnostics || null
    throw error
  }
  return data
}

export async function sendTryOn(personFile, product, analysis, boundingBox, signal) {
  const form = new FormData()
  form.append('image', personFile, personFile.name)
  form.append('product', JSON.stringify({
    product_id: String(product.product_id),
    product_name: product.product_name,
    brand: product.brand,
    image_url: product.image_url,
  }))
  form.append('analysis', JSON.stringify({
    selected_object: {
      product_type: analysis.selected_object?.product_type,
      category: analysis.selected_object?.category,
      description: analysis.selected_object?.description,
    },
    bounding_box: boundingBox,
  }))
  let response
  try {
    response = await fetch(`${API_URL}/api/circle-search/try-on`, { method: 'POST', body: form, signal })
  } catch (caught) {
    if (caught?.name === 'AbortError') throw caught
    throw new Error('가상 착용 서버에 연결할 수 없습니다. FastAPI 실행 상태를 확인하세요.')
  }
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = data?.detail
    throw new Error(`HTTP ${response.status}: ${typeof detail === 'string' ? detail : '가상 착용을 생성하지 못했습니다.'}`)
  }
  if (data?.status !== 'success' || typeof data.image_data_url !== 'string') {
    throw new Error('가상 착용 이미지가 포함되지 않은 응답입니다.')
  }
  return data
}
