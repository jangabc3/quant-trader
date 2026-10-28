"""AI 투자 회의.

구조는 TradingAgents (TauricResearch, Apache-2.0, https://github.com/TauricResearch/TradingAgents) 의
다중 에이전트 흐름을 따른다: 분석가 -> 팩트체크 -> 강세/약세 토론 -> 대표 최종 결정(5단계 등급).
같은 자료를 여러 번 읽던 역할(실적·수급·팀장·트레이더·리스크 3인)을 합쳐 7명으로 줄였다 (roles.py).
프롬프트·데이터 연결·코드는 QTrader 용으로 새로 작성했다 (토스증권 시세, 국내 수급, 한국어 보고서).
"""
