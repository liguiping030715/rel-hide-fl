@echo off
python experiments\correctness\run_v8_distributed_tcp_preflight.py
if errorlevel 1 exit /b %errorlevel%
python experiments\correctness\run_v8_docker_wsl_topology_preflight.py
