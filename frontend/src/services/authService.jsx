import { apiService } from './apiService';

class AuthService {
  async login(credentials) {
    try {
      const response = await apiService.post('/auth/login', credentials);
      const token = response.access_token || response.token;
      const user  = response.user || response;

      if (token) {
        localStorage.setItem('authToken', token);
        localStorage.setItem('authUser', JSON.stringify(user));
        apiService.setAuthToken(token);
      }

      return { success: true, user, token, access_token: token };
    } catch (error) {
      console.error('Login failed:', error);
      return { success: false, error: error.message || 'Login failed' };
    }
  }

  logout() {
    localStorage.removeItem('authToken');
    localStorage.removeItem('authUser');
    apiService.setAuthToken(null);
  }

  getCurrentUser() {
    try {
      return JSON.parse(localStorage.getItem('authUser') || 'null');
    } catch {
      return null;
    }
  }

  getToken() {
    return localStorage.getItem('authToken');
  }

  isAuthenticated() {
    return !!this.getToken();
  }
}

export const authService = new AuthService();
export default authService;
