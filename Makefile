.PHONY: install build dev serve start stop restart status logs collect health prune test

install:
	./install.sh

build:
	./server.sh build

dev:
	./server.sh dev

serve:
	./server.sh dev

start:
	./server.sh start

stop:
	./server.sh stop

restart:
	./server.sh restart

status:
	./server.sh status

logs:
	./server.sh logs

collect:
	./server.sh collect

health:
	./server.sh health

prune:
	./server.sh prune

test:
	uv run pytest -q
