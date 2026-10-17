.PHONY: regenerate
regenerate:
	python -m grpc_tools.protoc -I./src --python_out=./src --pyi_out=./src --grpc_python_out=./src ./src/town_of_salem.proto

install:
	pip install -r requirements.txt

env:
	source .venv/bin/activate

server:
	python3 src/server.py

client:
	python3 src/client.py