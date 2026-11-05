# Circle to Search 독립 테스트

React + Vite와 FastAPI로 만든 독립 기능 테스트입니다. 사진을 촬영하거나 업로드한 뒤 패션 상품 주위를 그리면, 원본 이미지 픽셀 기준의 경로·중심점·바운딩 박스를 계산합니다. FastAPI는 표시된 선택 영역과 사진을 OpenAI Vision에 한 번 보내 상품 정보, 검색어, **추정** 윤곽을 받습니다. 기존 ONFIT 서비스와 연결되지 않습니다.

## 구조

```text
CircleToSearch/
├── frontend/
│   ├── src/components/CameraView.jsx
│   ├── src/components/CircleSelector.jsx
│   ├── src/components/SelectionPreview.jsx
│   ├── src/services/api.js
│   ├── src/App.jsx, App.css, main.jsx
│   ├── .env.example
│   ├── package.json, package-lock.json
│   └── vite.config.js
├── backend/
│   ├── main.py
│   ├── routers/circle_search.py
│   ├── services/vision_service.py, vision_prompt.py
│   ├── services/recommendation_service.py 등 상품 검색 서비스
│   ├── services/sam_service.py
│   ├── .env.example
│   ├── requirements.txt, requirements-dev.txt
│   └── test_api.py, test_vision.py
├── .gitignore
├── start-dev.bat, start-mobile-test.bat
├── scripts/start-mobile-test.ps1
└── README.md
```

## PC에서 실행 (Windows, VS Code, Git Bash)

VS Code에서 이 폴더를 연 다음 터미널 두 개를 사용합니다. Python 3.10 이상과 Node.js 20.19 이상 또는 22.12 이상이 필요합니다.

터미널 1 — 백엔드:

```bash
cd /c/ajb/git/CircleToSearch/backend
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
# backend/.env의 OPENAI_API_KEY에 본인의 키를 입력하세요.
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8001
```

터미널 2 — 프론트엔드:

```bash
cd /c/ajb/git/CircleToSearch/frontend
npm install
cp .env.example .env.local
npm run dev
```

PC Chrome에서 `http://localhost:5173`을 엽니다. 카메라가 없는 PC는 **사진 업로드**로 테스트할 수 있습니다. API 상태는 `http://127.0.0.1:8001/health`와 프록시 경로 `http://localhost:5173/health`에서 확인합니다. 경로가 다르면 위 `cd` 명령을 자신의 프로젝트 위치에 맞게 바꾸세요. 포트 8001과 5173이 비어 있다면 루트의 `start-dev.bat`로 두 서버를 시작할 수도 있습니다.

`backend/.env`는 Git에서 제외됩니다. `OPENAI_VISION_MODEL`의 기본값은 `gpt-6-luna`이며 필요한 경우 해당 파일에서 변경할 수 있습니다. 키나 모델 값을 바꾼 뒤에는 FastAPI를 재시작하세요. API 키는 프론트엔드 환경변수에 넣지 마세요. 키가 없으면 `/segment`가 HTTP 503과 설정 안내를 반환하며 분석 결과를 꾸며 내지 않습니다.

브라우저는 기본적으로 같은 출처의 `/api/circle-search/segment`로 요청합니다. 개발 중 Vite가 `/api`와 `/health`를 로컬 FastAPI `http://127.0.0.1:8001`로 전달합니다. `frontend/.env.local`의 `VITE_API_URL`은 비워 둡니다. 나중에 별도 API 출처가 필요한 경우에만 설정하고 Vite를 재시작하세요. 이 변수는 브라우저 코드에 포함되므로 비밀 키를 넣지 마세요.

## 스마트폰 테스트 준비

Android Chrome 카메라는 HTTPS 또는 `localhost` 같은 보안 컨텍스트가 필요합니다. 휴대폰에서 `localhost`는 PC가 아닌 휴대폰 자신을 가리킵니다. [Cloudflare Quick Tunnel](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)은 계정이나 도메인 없이 PC의 5173번 포트에 임시 HTTPS 주소 하나를 연결합니다. 브라우저의 `/api` 요청도 같은 주소로 들어와 Vite가 8001번에 전달하므로 휴대폰에 로컬 API 주소를 설정하거나 FastAPI CORS를 넓힐 필요가 없습니다.

1. `cloudflared`가 없다면 [Cloudflare Windows 다운로드](https://developers.cloudflare.com/tunnel/downloads/)에서 64비트 실행 파일 또는 MSI를 설치합니다. Windows 패키지 관리자의 `winget install --id Cloudflare.cloudflared -e`도 가능합니다. 배치 파일은 PATH 또는 일반적인 MSI 설치 경로에서 실행 파일을 찾습니다.
2. Windows 탐색기에서 프로젝트 루트의 **`start-mobile-test.bat`를 더블클릭**합니다. VS Code와 Git Bash를 열 필요가 없습니다. 배치 파일은 8001·5173의 응답을 확인하여 CircleToSearch 서버면 재사용하고, 비어 있으면 시작합니다. 다른 앱이 포트를 사용하면 종료하지 않고 오류를 표시합니다.
3. 기존 Quick Tunnel이 이 배치 파일에서 시작되어 URL 기록이 남아 있다면 재사용합니다. 그렇지 않으면 새 터널을 열어 URL을 추출합니다. Vite의 정확한 임시 호스트 허용 설정을 갱신하고, 필요한 경우 **이 프로젝트의 Vite 프로세스인지 확인한 뒤에만** 재시작합니다. 완료되면 PC 브라우저가 열리고 CMD 창에 HTTPS 주소가 크게 표시됩니다. 임의의 8000번 서버는 건드리지 않습니다.
4. 스마트폰 Chrome에서 표시된 HTTPS 주소를 열고 카메라 권한 허용 → 촬영 → 손가락으로 원 그리기 → 폐곡선·미리보기 확인 → 전송 후 상품 정보와 `status: success` 확인 → 다시 촬영·다시 그리기 순서로 테스트합니다. 같은 주소의 `/health`는 `{"status":"ok"}`를 반환해야 합니다.
5. 테스트 후 **배치 파일 창에서 Enter**를 누르면 이 테스트의 Quick Tunnel이 종료됩니다. FastAPI와 Vite는 PC에서 계속 실행되며, 임시 허용 호스트는 제거됩니다. 실행 오류가 나면 창이 닫히지 않고 이유를 표시합니다. 로그는 프로젝트의 `.mobile-test/`에 저장됩니다. 설치·서버 상태만 확인하려면 `start-mobile-test.bat --check`를 사용할 수 있습니다.

임시 주소를 아는 사람은 테스트 페이지와 분석 API에 접근해 API 비용을 발생시킬 수 있습니다. 민감한 사진을 사용하지 마세요. 접근을 제한하려면 Windows 환경변수 `CIRCLE_TEST_EMAIL`에 본인 이메일을 설정한 뒤 배치 파일을 실행하세요. 이때 Cloudflare의 `--allowed-mail` 옵션으로 휴대폰에서 일회용 PIN을 요구합니다. 이전에 수동으로 연 터널이 이미 5173을 사용 중이고 URL 기록이 없다면 배치 파일은 그 프로세스를 종료하지 않고 안내합니다. 테스트가 끝나면 이 터널을 직접 종료한 뒤 다시 더블클릭하세요. 실제 스마트폰 동작은 사용자가 확인해야 합니다.

별도의 외부 프론트엔드에서 FastAPI를 직접 호출하는 경우에만 백엔드 `CORS_ORIGINS`에 정확한 출처를 설정합니다. 이번 단일 출처 Quick Tunnel 흐름에는 필요하지 않습니다. Quick Tunnel은 개발용이며 Render 배포나 ONFIT 통합은 여기서 수행하지 않았습니다.

## API 계약

`GET /health`는 `{"status":"ok"}`를 반환합니다. `POST /api/circle-search/segment`는 `multipart/form-data`로 `image` 파일, `width`, `height`, `point` (`[x,y]` JSON), `box` (`[left,top,right,bottom]` JSON), `polygon` (`[[x,y],...]` JSON)을 받습니다. 모든 좌표는 서버가 받은 원본 이미지의 픽셀 좌표입니다. 브라우저는 촬영·업로드 이미지를 최대 변 2400 px의 JPEG로 정규화하여 화면 표시와 서버 전송에 동일한 파일을 사용합니다.

서버는 JPEG/PNG/WebP, 최대 10 MB, 각 변 최대 8000 px를 허용합니다. 파일의 실제 형식과 크기, 좌표 범위, 다각형의 크기와 박스 일치 여부를 검증합니다. 정상 분석은 `status: "success"`, `selected_object`, `geometry`, `search_keywords`, `metadata`와 기존 `selection` 요약·`sam_enabled: false`를 반환합니다. 상품이 없으면 `status: "no_object"`, 키가 없으면 HTTP 503입니다. 잘못된 파일이나 좌표는 HTTP 413/415/422로 반환합니다. 모델은 선택선을 표시한 최대 1536 px 이미지 **한 장**만 받으며, 반환 좌표는 서버가 전송 이미지의 픽셀에서 원본 전송 이미지의 픽셀로 변환합니다. 추정 Polygon이 없거나 유효하지 않거나 재검토가 필요하면 핑크 오버레이를 표시하지 않습니다. 결과는 프로세스 메모리에 최대 32건만 캐시하며 파일·DB에 저장하지 않습니다.

## 유사 상품 추천

분석 결과가 표시된 뒤 `유사 상품 검색`을 누르면 브라우저가 원본 이미지의 바운딩 박스 기반 JPEG crop과 기존 Vision 설명·검색어를 `POST /api/circle-search/recommend`로 전송합니다. 서버는 Playwright Chromium으로 쇼핑 웹페이지에서 최대 30개 후보를 순차 검색하고, 번호를 붙인 상품 썸네일 모음과 crop을 OpenAI Vision 호출 한 번으로 비교해 최대 10개를 추천합니다. 같은 요청의 결과는 서버 메모리에만 캐시합니다. 웹페이지 접근 제한 또는 CAPTCHA가 감지되면 검색을 중단합니다. Chromium이 없다면 백엔드 가상환경에서 `python -m playwright install chromium`을 실행하세요.

## 검증

```bash
cd /c/ajb/git/CircleToSearch/frontend
npm run build
```

```bash
cd /c/ajb/git/CircleToSearch/backend
source .venv/Scripts/activate
python -m pip install -r requirements-dev.txt
python -m unittest -v test_vision test_api
```

`sam_service.py`는 사용되지 않는 기존 자리표시자입니다. 현재 정밀 분할 마스크와 이미지 편집은 없습니다. OpenAI Vision의 Polygon은 픽셀 단위 분할이 아닌 추정치입니다.
