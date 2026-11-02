import axios from 'axios'
import client from './axios'
import type { AuthLocale } from '../lib/authLocale'

function requireLocale(locale: AuthLocale): void {
  if (locale !== 'en' && locale !== 'es') throw new Error('Unsupported language')
}

function errorMessage(err: unknown): never {
  if (axios.isAxiosError(err)) {
    throw new Error(err.response?.data?.detail ?? err.message)
  }
  throw err
}

export async function register(body: {
  display_name: string
  email: string
  password: string
  phone?: string
}): Promise<{ email: string; message: string }> {
  return client.post('/auth/register', body).then(r => r.data).catch(errorMessage)
}

export async function login(body: {
  email: string
  password: string
}): Promise<{ expires_in: number }> {
  return client.post('/auth/login', body).then(r => r.data).catch(errorMessage)
}

export async function verify(body: {
  email: string
  code: string
  locale: AuthLocale
}): Promise<{ expires_in: number }> {
  requireLocale(body.locale)
  return client.post('/auth/verify', { email: body.email, code: body.code, locale: body.locale }).then(r => r.data).catch(errorMessage)
}

export async function resendCode(body: { email: string }): Promise<void> {
  return client.post('/auth/resend-code', body).then(r => r.data).catch(errorMessage)
}

export async function getMe(): Promise<{ id: string; email: string; role: string; display_name: string; phone: string | null; avatar_url: string | null }> {
  return client.get('/auth/me').then(r => r.data).catch(errorMessage)
}

export async function logout(): Promise<void> {
  return client.post('/auth/logout').then(() => undefined).catch(errorMessage)
}

export async function googleAuth(accessToken: string, locale: AuthLocale): Promise<{ expires_in: number }> {
  requireLocale(locale)
  return client.post('/auth/google', { access_token: accessToken, locale }).then(r => r.data).catch(errorMessage)
}

export async function forgotPassword(email: string): Promise<void> {
  return client.post('/auth/forgot-password', { email }).then(() => undefined).catch(errorMessage)
}

export async function resetPassword(token: string, new_password: string): Promise<void> {
  return client.post('/auth/reset-password', { token, new_password }).then(() => undefined).catch(errorMessage)
}
