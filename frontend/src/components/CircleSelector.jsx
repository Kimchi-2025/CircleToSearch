import React, { useEffect, useRef, useState } from 'react'

const MIN_STROKE_POINTS = 6
const BRIDGE_MAX_POINTS = 24
const ENDPOINT_SEARCH_FRACTION = 0.3
const LOOP_JOIN_PX = 32

function clamp(value, max) {
  return Math.max(0, Math.min(max, value))
}

function distance(a, b) {
  return Math.hypot(a[0] - b[0], a[1] - b[1])
}

function distanceToSegment(point, a, b) {
  const dx = b[0] - a[0]
  const dy = b[1] - a[1]
  const lengthSquared = dx * dx + dy * dy
  const t = lengthSquared ? Math.max(0, Math.min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / lengthSquared)) : 0
  return distance(point, [a[0] + t * dx, a[1] + t * dy])
}

function simplifyLine(points, tolerance) {
  const keep = new Set([0, points.length - 1])
  const stack = [[0, points.length - 1]]
  while (stack.length) {
    const [start, end] = stack.pop()
    let farthest = -1
    let maxDistance = tolerance
    for (let i = start + 1; i < end; i += 1) {
      const value = distanceToSegment(points[i], points[start], points[end])
      if (value > maxDistance) {
        maxDistance = value
        farthest = i
      }
    }
    if (farthest >= 0) {
      keep.add(farthest)
      stack.push([start, farthest], [farthest, end])
    }
  }
  return points.filter((_, index) => keep.has(index))
}

function simplifyRing(points, tolerance) {
  const middle = Math.floor(points.length / 2)
  const first = simplifyLine(points.slice(0, middle + 1), tolerance)
  const second = simplifyLine([...points.slice(middle), points[0]], tolerance)
  return [...first.slice(0, -1), ...second.slice(0, -1)]
}

function unitVector(from, to) {
  const length = distance(from, to)
  return length ? [(to[0] - from[0]) / length, (to[1] - from[1]) / length] : null
}

function cross(a, b, c) {
  return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
}

function segmentsIntersect(a, b, c, d) {
  if (Math.max(a[0], b[0]) < Math.min(c[0], d[0]) || Math.max(c[0], d[0]) < Math.min(a[0], b[0]) ||
      Math.max(a[1], b[1]) < Math.min(c[1], d[1]) || Math.max(c[1], d[1]) < Math.min(a[1], b[1])) return false
  const abC = cross(a, b, c)
  const abD = cross(a, b, d)
  const cdA = cross(c, d, a)
  const cdB = cross(c, d, b)
  return abC * abD <= 0 && cdA * cdB <= 0
}

function hasSelfIntersection(points) {
  for (let i = 0; i < points.length; i += 1) {
    const a = points[i]
    const b = points[(i + 1) % points.length]
    for (let j = i + 2; j < points.length; j += 1) {
      if (i === 0 && j === points.length - 1) continue
      if (segmentsIntersect(a, b, points[j], points[(j + 1) % points.length])) return true
    }
  }
  return false
}

function cubicPoint(a, c1, c2, b, t) {
  const s = 1 - t
  return [s ** 3 * a[0] + 3 * s ** 2 * t * c1[0] + 3 * s * t ** 2 * c2[0] + t ** 3 * b[0],
    s ** 3 * a[1] + 3 * s ** 2 * t * c1[1] + 3 * s * t ** 2 * c2[1] + t ** 3 * b[1]]
}

function segmentCrossPoint(a, b, c, d) {
  const ab = [b[0] - a[0], b[1] - a[1]]
  const cd = [d[0] - c[0], d[1] - c[1]]
  const denominator = ab[0] * cd[1] - ab[1] * cd[0]
  if (Math.abs(denominator) < 0.001) return null
  const ac = [c[0] - a[0], c[1] - a[1]]
  const t = (ac[0] * cd[1] - ac[1] * cd[0]) / denominator
  const u = (ac[0] * ab[1] - ac[1] * ab[0]) / denominator
  return t >= 0 && t <= 1 && u >= 0 && u <= 1 ? [a[0] + t * ab[0], a[1] + t * ab[1]] : null
}

function removeSmallLoops(input, scale) {
  const points = [...input]
  for (let i = 0; i < points.length - 3; i += 1) {
    let traveled = 0
    for (let j = i + 1; j < Math.min(points.length, i + 17); j += 1) {
      traveled += distance(points[j - 1], points[j])
      if (j - i < 3 || traveled < 8 * scale || traveled > 40 * scale || distance(points[i], points[j]) > 3 * scale) continue
      if (points.slice(i + 1, j).every((point) => distance(point, points[i]) <= 12 * scale)) {
        points.splice(i + 1, j - i)
        i = Math.max(-1, i - 2)
        break
      }
    }
  }
  return points
}

// A stroke may start or end with a tail outside the actual loop.
function endpointLoop(points, scale) {
  const count = points.length
  const earlyEnd = Math.floor(count * ENDPOINT_SEARCH_FRACTION)
  const lateStart = Math.ceil(count * (1 - ENDPOINT_SEARCH_FRACTION))
  const lengths = [0]
  for (let i = 1; i < count; i += 1) lengths[i] = lengths[i - 1] + distance(points[i - 1], points[i])
  let crossing = null
  for (let i = 0; i < earlyEnd; i += 1) {
    for (let j = lateStart; j < count - 1; j += 1) {
      const point = segmentCrossPoint(points[i], points[i + 1], points[j], points[j + 1])
      if (lengths[j] - lengths[i] < 0.6 * lengths[count - 1]) continue
      if (point && (!crossing || j - i > crossing.j - crossing.i)) crossing = { i, j, point }
    }
  }
  if (crossing) return [crossing.point, ...points.slice(crossing.i + 1, crossing.j + 1), crossing.point]

  const fullGap = distance(points[0], points.at(-1))
  let best = null
  for (let i = 1; i <= earlyEnd; i += 1) {
    for (let j = lateStart; j < count; j += 1) {
      if (lengths[j] - lengths[i] < 0.6 * lengths[count - 1]) continue
      const gap = distance(points[i], points[j])
      if (gap > LOOP_JOIN_PX * scale) continue
      const score = gap + 0.12 * (lengths[i] + lengths[count - 1] - lengths[j])
      if (score < 0.85 * fullGap && (!best || score < best.score)) best = { i, j, score }
    }
  }
  return best ? points.slice(best.i, best.j + 1) : null
}

function closeAndSmooth(points, scale, width, height) {
  if (points.length < MIN_STROKE_POINTS) return null
  const xs = points.map((point) => point[0])
  const ys = points.map((point) => point[1])
  const minX = Math.min(...xs)
  const maxX = Math.max(...xs)
  const minY = Math.min(...ys)
  const maxY = Math.max(...ys)
  const span = Math.hypot(maxX - minX, maxY - minY)
  if (span < 12 * scale) return null
  const start = points[0]
  const last = points.at(-1)
  const gap = distance(last, start)
  const lookDistance = Math.min(12 * scale, 0.12 * span)
  const afterStart = points.find((point, index) => index > 0 && distance(point, start) >= lookDistance) || points[1]
  const beforeEnd = points.findLast((point, index) => index < points.length - 1 && distance(point, last) >= lookDistance) || points.at(-2)
  const outgoing = unitVector(beforeEnd, last)
  const incoming = unitVector(start, afterStart)
  if (!outgoing || !incoming) return null

  for (const handleRatio of gap <= 12 * scale ? [0.35, 0.15, 0] : [0.35, 0.15]) {
    const candidate = [...points]
    if (gap > 1.5 * scale) {
      const handle = Math.min(handleRatio * gap, 0.18 * span)
      const c1 = [last[0] + outgoing[0] * handle, last[1] + outgoing[1] * handle]
      const c2 = [start[0] - incoming[0] * handle, start[1] - incoming[1] * handle]
      const steps = Math.min(BRIDGE_MAX_POINTS, Math.max(3, Math.ceil(gap / (3 * scale))))
      const margin = 0.2 * span
      for (let i = 1; i < steps; i += 1) {
        const point = cubicPoint(last, c1, c2, start, i / steps)
        if (point[0] < minX - margin || point[0] > maxX + margin ||
            point[1] < minY - margin || point[1] > maxY + margin) break
        candidate.push(point)
      }
      if (candidate.length !== points.length + steps - 1) continue
    } else if (gap <= 1.5 * scale) candidate.pop()

    const spaced = candidate.filter((point, index) => index === 0 || distance(point, candidate[index - 1]) >= 1.25 * scale)
    if (spaced.length < 3) continue
    const smoothed = spaced.length < 8 ? spaced : spaced.map((point, index) => {
      const prev = spaced[(index - 1 + spaced.length) % spaced.length]
      const next = spaced[(index + 1) % spaced.length]
      return [0.125 * prev[0] + 0.75 * point[0] + 0.125 * next[0],
        0.125 * prev[1] + 0.75 * point[1] + 0.125 * next[1]]
    })
    const simplified = simplifyRing(smoothed, 0.8 * scale)
    if (simplified.length < 3 || simplified.some(([x, y]) => x < 0 || y < 0 || x >= width || y >= height)) continue
    const selection = createSelection(simplified, width, height)
    if (!selection) continue
    const area = Math.abs(simplified.reduce((sum, point, index) => {
      const next = simplified[(index + 1) % simplified.length]
      return sum + point[0] * next[1] - next[0] * point[1]
    }, 0)) / 2
    const [left, top, right, bottom] = selection.box
    if (area / ((right - left) * (bottom - top)) < 0.08) continue
    return simplified
  }
  return null
}

// Keep the submitted polygon and both canvas views on the same processed path.
export function finishStroke(rawPoints, scale, width = 100000, height = 100000) {
  if (rawPoints.length < MIN_STROKE_POINTS) return null
  let points = [...rawPoints]
  const start = points[0]
  const end = points.at(-1)

  // Remove a short excursion that returns to its own tail.
  for (let i = Math.max(2, points.length - 24); i < points.length - 2; i += 1) {
    if (distance(points[i], end) > 5 * scale) continue
    if (points.slice(i + 1).some((point) => distance(point, points[i]) > 12 * scale)) {
      points = points.slice(0, i + 1)
      break
    }
  }

  // If the pen already passed the start, discard only that short overshoot.
  for (let i = Math.max(3, points.length - 8); i < points.length; i += 1) {
    if (distance(points[i], start) <= 6 * scale) {
      points = points.slice(0, i + 1)
      break
    }
  }
  points = removeSmallLoops(points, scale)
  if (points.length < MIN_STROKE_POINTS) return null
  const trimmed = endpointLoop(points, scale)
  return (trimmed && closeAndSmooth(trimmed, scale, width, height)) || closeAndSmooth(points, scale, width, height)
}

function chooseInteriorPoint(polygon, box) {
  const y = (box[1] + box[3]) / 2 + 0.001
  const crossings = []
  for (let i = 0; i < polygon.length; i += 1) {
    const a = polygon[i]
    const b = polygon[(i + 1) % polygon.length]
    if ((a[1] <= y && b[1] > y) || (b[1] <= y && a[1] > y)) {
      crossings.push(a[0] + ((y - a[1]) * (b[0] - a[0])) / (b[1] - a[1]))
    }
  }
  crossings.sort((a, b) => a - b)
  let best = [(box[0] + box[2]) / 2, (box[0] + box[2]) / 2]
  for (let i = 0; i + 1 < crossings.length; i += 2) {
    if (crossings[i + 1] - crossings[i] > best[1] - best[0]) best = [crossings[i], crossings[i + 1]]
  }
  return [Math.round((best[0] + best[1]) / 2), Math.round(y)]
}

export function createSelection(points, width, height) {
  const polygon = points.map(([x, y]) => [
    Math.round(clamp(x, width - 1)),
    Math.round(clamp(y, height - 1)),
  ]).filter((point, index, items) => index === 0 || point[0] !== items[index - 1][0] || point[1] !== items[index - 1][1])
  if (polygon.length > 1 && polygon[0][0] === polygon.at(-1)[0] && polygon[0][1] === polygon.at(-1)[1]) polygon.pop()
  if (polygon.length < 3) return null
  const xs = polygon.map((point) => point[0])
  const ys = polygon.map((point) => point[1])
  const box = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]
  const signedArea = polygon.reduce((sum, point, index) => {
    const next = polygon[(index + 1) % polygon.length]
    return sum + point[0] * next[1] - next[0] * point[1]
  }, 0) / 2
  if (box[2] - box[0] < 8 || box[3] - box[1] < 8 || Math.abs(signedArea) < 64 || hasSelfIntersection(polygon)) return null
  return { polygon, box, point: chooseInteriorPoint(polygon, box) }
}

export default function CircleSelector({ image, selection, onSelectionChange, visionGeometry,
  mobile = false, hideSelection = false, locked = false, onLockedPointerDown }) {
  const imageRef = useRef(null)
  const stageRef = useRef(null)
  const canvasRef = useRef(null)
  const pointsRef = useRef([])
  const drawingRef = useRef(false)
  const pointerIdRef = useRef(null)
  const [draft, setDraft] = useState([])
  const [size, setSize] = useState({ width: 0, height: 0 })
  const [error, setError] = useState('')

  useEffect(() => {
    const element = mobile ? stageRef.current : imageRef.current
    if (!element) return undefined
    const measure = () => {
      const rect = element.getBoundingClientRect()
      if (mobile) {
        const scale = Math.min(rect.width / image.width, rect.height / image.height)
        const width = image.width * scale
        const height = image.height * scale
        setSize({ width, height, left: (rect.width - width) / 2, top: (rect.height - height) / 2 })
      } else {
        setSize({ width: rect.width, height: rect.height })
      }
    }
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    measure()
    return () => observer.disconnect()
  }, [image.url, image.width, image.height, mobile])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !size.width || !size.height) return
    const ratio = window.devicePixelRatio || 1
    canvas.width = Math.round(size.width * ratio)
    canvas.height = Math.round(size.height * ratio)
    const context = canvas.getContext('2d')
    context.setTransform(canvas.width / size.width, 0, 0, canvas.height / size.height, 0, 0)
    context.clearRect(0, 0, size.width, size.height)
    if (selection && !draft.length && visionGeometry?.polygon && !visionGeometry.needs_review) {
      context.beginPath()
      visionGeometry.polygon.forEach(([x, y], index) => {
        const dx = (x / image.width) * size.width
        const dy = (y / image.height) * size.height
        if (index === 0) context.moveTo(dx, dy)
        else context.lineTo(dx, dy)
      })
      context.closePath()
      context.fillStyle = 'rgba(255, 40, 160, 0.35)'
      context.fill()
      return
    }
    if (hideSelection && selection && !draft.length) return
    const points = draft.length ? draft : selection?.polygon || []
    if (!points.length) return
    context.beginPath()
    points.forEach(([x, y], index) => {
      const dx = (x / image.width) * size.width
      const dy = (y / image.height) * size.height
      if (index === 0) context.moveTo(dx, dy)
      else context.lineTo(dx, dy)
    })
    if (selection && !draft.length) context.closePath()
    context.lineWidth = 3
    context.strokeStyle = '#c9fb62'
    context.shadowColor = '#101b25'
    context.shadowBlur = 5
    context.stroke()
    context.shadowBlur = 0
    if (selection && !draft.length) {
      context.fillStyle = 'rgba(201, 251, 98, 0.2)'
      context.fill()
    }
  }, [draft, selection, visionGeometry, size, image.width, image.height, hideSelection])

  function getPoint(event) {
    const rect = canvasRef.current.getBoundingClientRect()
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) return null
    return [
      clamp(((event.clientX - rect.left) / rect.width) * image.width, image.width - 1),
      clamp(((event.clientY - rect.top) / rect.height) * image.height, image.height - 1),
    ]
  }

  function pointerDown(event) {
    if (locked) {
      onLockedPointerDown?.()
      return
    }
    if (drawingRef.current) return
    if (event.pointerType === 'mouse' && event.button !== 0) return
    const point = getPoint(event)
    if (!point) return
    event.preventDefault()
    canvasRef.current.setPointerCapture(event.pointerId)
    drawingRef.current = true
    pointerIdRef.current = event.pointerId
    pointsRef.current = [point]
    setDraft([...pointsRef.current])
    setError('')
    onSelectionChange(null)
  }

  function pointerMove(event) {
    if (!drawingRef.current || event.pointerId !== pointerIdRef.current) return
    event.preventDefault()
    const point = getPoint(event)
    if (!point) return
    const last = pointsRef.current.at(-1)
    const rect = canvasRef.current.getBoundingClientRect()
    const scale = Math.max(image.width / rect.width, image.height / rect.height)
    if (distance(point, last) < Math.max(2, 1.25 * scale)) return
    if (pointsRef.current.length >= 2015) return
    pointsRef.current.push(point)
    setDraft([...pointsRef.current])
  }

  function pointerUp(event) {
    if (!drawingRef.current || event.pointerId !== pointerIdRef.current) return
    event.preventDefault()
    drawingRef.current = false
    pointerIdRef.current = null
    const rect = canvasRef.current.getBoundingClientRect()
    const scale = Math.max(image.width / rect.width, image.height / rect.height)
    const point = getPoint(event)
    if (point && distance(point, pointsRef.current.at(-1)) <= 24 * scale) {
      pointsRef.current.push(point)
    }
    const path = finishStroke(pointsRef.current, scale, image.width, image.height)
    const result = path && createSelection(path, image.width, image.height)
    setDraft([])
    pointsRef.current = []
    if (!result) {
      setError('영역을 다시 그려주세요.')
      onSelectionChange(null)
    } else {
      onSelectionChange(result)
    }
  }

  function pointerCancel(event) {
    if (event && event.pointerId !== pointerIdRef.current) return
    drawingRef.current = false
    pointerIdRef.current = null
    pointsRef.current = []
    setDraft([])
  }

  function reset() {
    pointerCancel()
    setError('')
    onSelectionChange(null)
  }

  return (
    <section className={mobile ? 'mobile-selector' : 'card'} aria-label="영역 선택">
      {!mobile && <><div className="section-heading"><span className="step">02</span><h2>물체 둘러 그리기</h2></div>
      <p className="hint">사진 속 물체의 바깥선을 손가락이나 마우스로 한 번에 그리세요.</p></>}
      <div ref={stageRef} className={mobile ? 'image-stage mobile-image-stage' : 'image-stage'}>
        <img ref={imageRef} src={image.url} alt="선택할 원본 사진" draggable="false" />
        <canvas ref={canvasRef} className="drawing-canvas"
          style={mobile ? { left: size.left || 0, top: size.top || 0,
            width: size.width, height: size.height, right: 'auto', bottom: 'auto' } : undefined}
          onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp}
          onPointerCancel={pointerCancel} aria-label="사진 위에 선택 영역 그리기" />
      </div>
      {!mobile && <div className="button-row"><button className="secondary" onClick={reset}>선택 초기화 · 다시 그리기</button></div>}
      {error && <p className={mobile ? 'mobile-drawing-error' : 'error'} role="alert">{error}</p>}
    </section>
  )
}
