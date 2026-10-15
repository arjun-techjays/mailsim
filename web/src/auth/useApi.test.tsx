import { renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { SessionContext } from './session';
import { useApi, useRole } from './session';

describe('useApi', () => {
  it('throws when not connected and exposes the role otherwise', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const none = ({ children }: { children: ReactNode }) => (
      <SessionContext.Provider value={{ session: null, api: null, connect: () => {}, disconnect: () => {}, rejected: false, probing: false, localNoAuthLink: false }}>
        {children}
      </SessionContext.Provider>
    );
    expect(() => renderHook(() => useApi(), { wrapper: none })).toThrow('not connected');
    expect(renderHook(() => useRole(), { wrapper: none }).result.current).toBeNull();
  });
});
