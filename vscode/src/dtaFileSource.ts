// A ByteSource over a local file, so the dta viewer never loads more of a
// dataset than the rows on screen.
//
// Each read opens, reads, and closes. Holding one handle for the life of the
// viewer would be marginally faster, but on Windows an open handle can make
// Stata's `save, replace` on the same file fail — and re-saving a dataset
// while looking at it is the normal workflow.

import { promises as fs } from "node:fs";

import type { ByteSource } from "./dtaReader";

export async function openFileByteSource(path: string): Promise<ByteSource> {
  const { size } = await fs.stat(path);
  return {
    size,
    async read(offset: number, length: number): Promise<Uint8Array> {
      const start = Math.min(Math.max(0, offset), size);
      const want = Math.min(Math.max(0, length), size - start);
      if (want === 0) return new Uint8Array(0);
      const handle = await fs.open(path, "r");
      try {
        const buffer = Buffer.allocUnsafe(want);
        let got = 0;
        while (got < want) {
          const { bytesRead } = await handle.read(buffer, got, want - got, start + got);
          if (bytesRead === 0) break; // the file shrank underneath us
          got += bytesRead;
        }
        return buffer.subarray(0, got);
      } finally {
        await handle.close();
      }
    },
  };
}
