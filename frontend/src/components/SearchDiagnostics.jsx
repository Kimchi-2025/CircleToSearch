import React from 'react'

const statusLabel = { executed: '실행', skipped: '미실행', failed: '실패' }
const reasonLabel = {
  unknown_id: '검색 후보에 없는 ID',
  duplicate_id: '중복 ID',
  not_similar: '관련 없는 상품',
  over_limit: '추천 10개 초과',
}

function count(value) {
  return Number.isFinite(value) ? value : '—'
}

function seconds(value) {
  return Number.isFinite(value) ? `${(value / 1000).toFixed(1)}초` : '—'
}

function kilobytes(value) {
  return Number.isFinite(value) ? `${(value / 1024).toFixed(0)}KB` : '—'
}

function dimensions(width, height) {
  return Number.isFinite(width) && Number.isFinite(height) ? `${width}×${height}px` : '—'
}

export default function SearchDiagnostics({ data }) {
  const details = data && typeof data === 'object' && !Array.isArray(data) ? data : {}
  const comparison = details.vision_comparison && typeof details.vision_comparison === 'object' &&
    !Array.isArray(details.vision_comparison) ? details.vision_comparison : null
  const queryStats = Array.isArray(details.query_stats) ? details.query_stats.filter(
    (query) => query && typeof query === 'object' && !Array.isArray(query)) : []
  const reasons = comparison?.excluded_reasons
  const exclusions = reasons && typeof reasons === 'object' && !Array.isArray(reasons)
    ? Object.entries(reasons).filter(([, value]) => Number.isFinite(value) && value > 0) : []

  return <section className="search-diagnostics" aria-label="검색 및 AI 추천 분석">
    <h3>검색 및 AI 추천 분석</h3>
    <dl className="diagnostic-summary">
      <div><dt>실행한 검색어</dt><dd>{count(details.executed_query_count)}개</dd></div>
      <div><dt>최종 후보</dt><dd>{count(details.candidate_count)}개</dd></div>
      <div><dt>AI 추천</dt><dd>{count(details.recommended_count)}개</dd></div>
      <div><dt>전체 소요</dt><dd>{seconds(details.total_elapsed_ms)}</dd></div>
    </dl>
    <div className="diagnostic-table-wrap">
      <table className="diagnostic-table">
        <caption>검색어별 결과</caption>
        <thead><tr><th scope="col">검색어</th><th scope="col">상태</th><th scope="col">수집 카드</th><th scope="col">신규 후보</th><th scope="col">소요</th></tr></thead>
        <tbody>{queryStats.map((query, index) => <tr key={`${index}-${query.keyword}`}>
          <th scope="row">{typeof query.keyword === 'string' ? query.keyword : '검색어 정보 없음'}</th>
          <td>{statusLabel[query.status] || (typeof query.status === 'string' ? query.status : '확인 불가')}</td>
          <td>{query.status === 'skipped' ? '—' : count(query.collected_count)}</td>
          <td>{query.status === 'skipped' ? '—' : count(query.added_count)}</td>
          <td>{query.status === 'skipped' ? '—' : seconds(query.elapsed_ms)}</td>
        </tr>)}</tbody>
      </table>
    </div>
    <p className="diagnostic-note">수집 카드는 Playwright가 실제 읽은 유효 상품 카드 수입니다. 중복·종류 필터와 최대 30개 선정으로 신규 후보 합계는 최종 후보 수와 다를 수 있습니다.</p>
    <h4>OpenAI Vision 비교</h4>
    {comparison ? <>
      <p>전달 후보 {count(comparison.input_candidate_count)}개 · AI 선택 {count(comparison.selected_count)}개 · 서버 검증 후 {count(comparison.final_count)}개 · 제외 {count(comparison.excluded_count)}개</p>
      {exclusions.length > 0 && <p>제외 사유: {exclusions.map(([key, value]) => `${reasonLabel[key] || key} ${value}개`).join(' · ')}</p>}
      <p>이미지 비교 {seconds(comparison.elapsed_ms)} · 후보 이미지 준비 {seconds(comparison.contact_sheet_elapsed_ms)}</p>
      {Number.isFinite(comparison.input_image_bytes) && <p>OpenAI 입력 이미지 {count(comparison.input_image_count)}개 · Crop {dimensions(comparison.crop_width_px, comparison.crop_height_px)} / {kilobytes(comparison.crop_image_bytes)} · Contact Sheet {dimensions(comparison.contact_sheet_width_px, comparison.contact_sheet_height_px)} / {kilobytes(comparison.contact_sheet_image_bytes)} · 합계 {kilobytes(comparison.input_image_bytes)} · 출력 {count(comparison.output_tokens)}토큰</p>}
    </> : <p>이미지 비교 결과가 없습니다.</p>}
  </section>
}
