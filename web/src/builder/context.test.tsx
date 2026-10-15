import { render } from '@testing-library/react';
import { useBuilder } from './context';

function Outside() {
  useBuilder();
  return null;
}

it('useBuilder fails loudly outside the builder', () => {
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
  expect(() => render(<Outside />)).toThrow('useBuilder must be used inside the scenario builder');
});
