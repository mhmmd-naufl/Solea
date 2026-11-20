import { Footprints } from 'lucide-react'
import { BUSINESS_NAME } from '../config'

/**
 * Ikon identitas aplikasi yang generik (bukan logo merek tertentu).
 *
 * Nama yang tampil di tooltip/alt diambil dari BUSINESS_NAME, jadi bisa
 * diganti lewat VITE_BUSINESS_NAME tanpa menyunting komponen ini.
 */
export default function Logo({
  variant = 'biru',
  className = 'h-8 w-8',
  fallbackClassName = 'bg-primary-600 rounded-xl',
}) {
  const warna = variant === 'putih' ? 'text-white' : 'text-primary-600'

  return (
    <span
      className={`flex items-center justify-center shrink-0 ${className} ${fallbackClassName}`}
      title={BUSINESS_NAME}
    >
      <Footprints className={`h-[60%] w-[60%] ${warna}`} aria-label={BUSINESS_NAME} />
    </span>
  )
}
