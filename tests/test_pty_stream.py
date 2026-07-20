import pty
import os
import subprocess
import time

def main():
    master, slave = pty.openpty()
    # pass stdin=subprocess.PIPE so it is non-interactive but stdout goes to slave PTY
    p = subprocess.Popen(
        ['claude', '-p', 'write a 300-word story about a spaceship', '--safe-mode'],
        stdout=slave,
        stderr=subprocess.PIPE,
        stdin=subprocess.PIPE,
        text=True
    )
    os.close(slave)
    
    t0 = time.time()
    times = []
    chunks = []
    
    while True:
        try:
            chunk = os.read(master, 1024)
            if not chunk:
                break
            print(f"[{time.time() - t0:.4f}s] Chunk ({len(chunk)} bytes): {chunk.decode('utf-8', errors='replace')[:40]}...")
            chunks.append(chunk)
            times.append(time.time() - t0)
        except OSError:
            break
            
    print("Chunks received:", len(chunks))
    intervals = [times[i+1]-times[i] for i in range(len(times)-1)]
    print("Intervals (first 15):", [round(t, 4) for t in intervals[:15]])
    print("Total output:")
    print(b"".join(chunks).decode('utf-8', errors='replace'))

if __name__ == '__main__':
    main()
