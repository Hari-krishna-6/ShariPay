'use client';
import './reactbits.css';
export default function GlareHover({ width='100%', height='auto', background='#100d18', borderRadius='20px', borderColor='rgba(221,195,255,.18)', children, glareColor='#b98bff', glareOpacity=.28, glareAngle=-45, glareSize=250, transitionDuration=650, playOnce=false, className='', style={} }) {
  const hex=glareColor.replace('#',''); let rgba=glareColor; if(/^[0-9A-Fa-f]{6}$/.test(hex)){const r=parseInt(hex.slice(0,2),16),g=parseInt(hex.slice(2,4),16),b=parseInt(hex.slice(4,6),16);rgba=`rgba(${r}, ${g}, ${b}, ${glareOpacity})`}
  return <div className={`glare-hover ${playOnce?'glare-hover--play-once':''} ${className}`} style={{'--gh-width':width,'--gh-height':height,'--gh-bg':background,'--gh-br':borderRadius,'--gh-angle':`${glareAngle}deg`,'--gh-duration':`${transitionDuration}ms`,'--gh-size':`${glareSize}%`,'--gh-rgba':rgba,'--gh-border':borderColor,...style}}>{children}</div>
}
