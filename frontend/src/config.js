// Identitas aplikasi yang bisa diganti tanpa menyunting komponen.
// Atur VITE_BUSINESS_NAME di frontend/.env untuk memakai nama usaha sendiri.
// Default-nya "Solea" (nama produk); ubah sesuai merek klien/mu.
export const BUSINESS_NAME =
  import.meta.env.VITE_BUSINESS_NAME?.trim() || "Solea";
