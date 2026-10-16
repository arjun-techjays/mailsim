import { REVOKE_AFTER_MS, saveBlob } from './download';

describe('saveBlob', () => {
  const realCreate = URL.createObjectURL;
  const realRevoke = URL.revokeObjectURL;
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    URL.createObjectURL = realCreate;
    URL.revokeObjectURL = realRevoke;
  });

  it('saves octet-stream bytes under a sanitised name and revokes the URL after a delay', () => {
    vi.useFakeTimers();
    const blobs: Blob[] = [];
    URL.createObjectURL = vi.fn((b: Blob) => {
      blobs.push(b);
      return 'blob:x';
    });
    URL.revokeObjectURL = vi.fn();
    const anchors: HTMLAnchorElement[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      anchors.push(this);
    });
    const name = saveBlob(new Blob(['<svg onload=alert(1)>'], { type: 'image/svg+xml' }), ['..', 'pic\u202Egnp.svg']);
    expect(name).toBe('picgnp.svg');
    expect(anchors[0]?.download).toBe('picgnp.svg');
    expect(anchors[0]?.rel).toBe('noopener');
    expect(blobs[0]?.type).toBe('application/octet-stream');
    expect(URL.revokeObjectURL).not.toHaveBeenCalled();
    vi.advanceTimersByTime(REVOKE_AFTER_MS);
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:x');
    expect(saveBlob('text', [null], 'fallback.bin')).toBe('fallback.bin');
  });

  it('sanitises the fallback name too, and uses "download" when nothing is usable', () => {
    URL.createObjectURL = vi.fn(() => 'blob:y');
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    expect(saveBlob('x', [null], 'attachment-../\u202Eexe.txt')).toBe('exe.txt');
    expect(saveBlob('x', ['..'], '..')).toBe('download');
  });
});
