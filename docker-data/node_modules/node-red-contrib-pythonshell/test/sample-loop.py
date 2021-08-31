import time
import sys

start = time.time()

i = 0
while True:
	now = time.time()
	lapsed = now - start
	if lapsed > 4:
		print( "loop ended")
		break
	else:
		print("on going")
		i += 1
	time.sleep(1)
