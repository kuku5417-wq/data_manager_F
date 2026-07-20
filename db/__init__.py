"""db — 운영서버(MSSQL) 전송 모듈 (휴면).

data_manager는 parquet 생산을 유지하면서, 테이블형 parquet을 MSSQL(jsh_* 테이블)로 push 한다.
.env DB_SEVER/DB_PORT/DB_NAME/DB_USERNAME/DB_PASSWORD 가 설정될 때만 동작(미설정 시 휴면).
드라이버(pyodbc)는 lazy import — 미설치/서버 부재에도 앱은 안전.
"""
