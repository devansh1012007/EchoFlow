import { createExternalShareLink } from '../endpoints/clips';
import { apiFetch } from '../client';

jest.mock('../client', () => ({ apiFetch: jest.fn(), API_BASE_URL: 'https://api.example', getTokenStore: jest.fn() }));
const mockApiFetch = apiFetch as jest.MockedFunction<typeof apiFetch>;

describe('createExternalShareLink', () => {
  it('uses the owner-only share-link endpoint and returns its canonical URL', async () => {
    mockApiFetch.mockResolvedValue({
      clip_id: 'clip-1',
      url: 'https://app.echoflow.in/clip/clip-1?s=share-token',
      expires_in: 2_592_000,
    });

    await expect(createExternalShareLink('clip-1')).resolves.toEqual({
      url: 'https://app.echoflow.in/clip/clip-1?s=share-token', expiresIn: 2_592_000,
    });
    expect(mockApiFetch).toHaveBeenCalledWith('/clips/clip-1/share-link/', { method: 'POST' });
  });

  it('refuses to invent a public hostname when the API is not configured', async () => {
    mockApiFetch.mockResolvedValue({ clip_id: 'clip-1', url: null, expires_in: 2_592_000 });
    await expect(createExternalShareLink('clip-1')).rejects.toThrow('PUBLIC_APP_BASE_URL');
  });
});
