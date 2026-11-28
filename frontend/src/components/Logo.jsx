import { useState } from 'react'
import { Footprints } from 'lucide-react'
import { BUSINESS_NAME } from '../config'

// Dua varian warna, masing-masing SVG dulu (tajam di semua ukuran) lalu PNG.
//   biru  -> untuk latar terang (navbar, kartu login, header panel)
//   putih -> untuk latar gelap (panel kiri halaman daftar, bg primary)
const SUMBER = {
  biru: ['/assets/logo-biru.svg', '/assets/logo-biru.png'],
  putih: ['/assets/logo-putih.svg', '/assets/logo-putih.png'],
}

/**
 * Logo brand (file SVG/PNG di `frontend/public/assets/`). Kalau gagal
 * dimuat -- misalnya berkas terhapus -- komponen jatuh kembali ke ikon
 * Footprints, jadi header tidak pernah menampilkan gambar rusak.
 */
export default function Logo({ variant = 'biru', className = 'h-8 w-8', fallbackClassName = 'bg-primary-600 rounded-xl' }) {
  const [indeks, setIndeks] = useState(0)
  const sumber = SUMBER[variant] || SUMBER.biru

  if (indeks >= sumber.length) {
    return (
      <span className={`flex items-center justify-center shrink-0 ${className} ${fallbackClassName}`}>
        <Footprints className="h-[60%] w-[60%] text-white" />
      </span>
    )
  }

  return (
    <img
      src={sumber[indeks]}
      alt={`Logo ${BUSINESS_NAME}`}
      className={`object-contain shrink-0 ${className}`}
      onError={() => setIndeks((i) => i + 1)}
    />
  )
}
