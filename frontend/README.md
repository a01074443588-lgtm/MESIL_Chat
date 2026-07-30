# MESIL_Chat 프런트엔드

React 기반 직원용 모바일 웹앱입니다. 영구 데이터와 인증은 FastAPI 백엔드가 담당합니다.

```powershell
npm install
npm run dev -- --host 0.0.0.0 --port 3100
npm run build
npm run lint
npm test
```

- `app/components`: 로그인·채팅·관리자·PWA 화면
- `public/manifest.webmanifest`: 홈 화면 설치정보
- `public/sw.js`: 앱 셸과 오프라인 안내
- `.openai/hosting.json`: Sites 호환 빌드 설정이며 DB·파일 저장은 사용하지 않음

전체 실행·시험·보안 내용은 프로젝트 루트 `README.md`와 `docs`를 확인합니다.
