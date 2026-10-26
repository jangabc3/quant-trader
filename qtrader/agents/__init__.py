"""AI 투자 회의.

구조는 TradingAgents (TauricResearch, Apache-2.0, https://github.com/TauricResearch/TradingAgents) 의
다중 에이전트 흐름을 따른다: 분석가 4명 -> 강세/약세 토론 -> 리서치 팀장 -> 트레이더 ->
리스크 위원회(공격/중립/보수) 토론 -> 포트폴리오 매니저 최종 결정(5단계 등급).
프롬프트·데이터 연결·코드는 QTrader 용으로 새로 작성했다 (토스증권 시세, 국내 수급, 한국어 보고서).
"""
