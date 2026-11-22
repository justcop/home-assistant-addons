"""Full-frame GTA VI sunset artwork for the 52x32 Govee H6631.

Adapted from the user's original VI logo, cropped closely to reduce unused
background LEDs. Pre-quantised to a 64-colour palette for a small, dependency-free
runtime asset; all 1664 display pixels are covered.
"""
import base64
import zlib

WIDTH = 52
HEIGHT = 32
_PALETTE_COLOURS = 64
_PACKED = "c-n20O^72!6vwMO-I;uJPrB2cr1N1uCh6&<lm19mI+IBznS(4M3=8fm$O^;iVH^;TG6z9q54v7F$)X1ZQ4j=AyC8ZJS@!CR7hROyyC)AGWS4E<tIm7~{!`S)d++yNrMs%*J0!j(#vds0$@Zhqw*Gj}zx(0d!Mi`lhnM45t@!!Z<LAQPuN~jMa@{<NO9yexZpHQ|@#Ipxefg>N?bH07$JN8X)i>@pZ~m5h^V81BN6L?f_FqT&?{)V{MgQ{&dGHVU;W4=#lg}TKU+$5geih!nOOC%J55Fcizag)EOs?M`4{ngJ-Y17A<nV}m_X4^1B6;T$dFz<G^fI}2K(0PZUSZ@5Q@Fn%SGUO(kIWjxE0BRmDm&~y+KB>x=sE$5C>Q%bg{qX6)2EWuIU+7gOX+hXRdFSsiZWH&JlBTA0>`N`KYeaX%QN}Ha+A<iC~Ooq3r$p^P)MsgPIbtnjl!umB{q}QLgFsKDA9dZlVGyT1$Jr2uQD~=#Gn?&rPT$K)n;qA1&iixs{qU@lsCW)LaL>zEQqAWdev-h$ZR#^%x<gEz)te^_ZObrNQ7|>nL%2dwOVMCgT->DDBKAQ*nnBBW~#P^8sl+GQK<~$I5B9-VlQF9hON<;!_{)R2$g5XsL`lD8jlsFj`Kd3LKpKrNZKEP2U@>B$^f&sH>+zSR2V|!xURvNEXQVTF`Xg<juBY<{cxlpGoR1Qc9IrD>-7=*>U^!S=_H!ZENg`ENz+0=rc>sb?H<Sm@mRapLqpq}2f<{@EYs$$x^4H`$V{UkqIP>N&Y^m+u-Rl71QD~%b5GCopv=S{Mo_5T>GTpo08Tq5jojhTpD-H@HvG^I%ybeAoguB4wzfKFYV&@;;JCWO0O};>9y&PVyH3@oTc}Q_#K%&p)G3+NuWUPx>odEA`I3M$rc#03L14)&bvb03rasuIRH{|S86pE|w$>hCU~AP%*`bzh>bl<TLW54%Eb5dIs=BZ@P&ZMD*BQFN7`~@#n%3=V$l%lqa^*5o!0==|o!&j$Te(=I0|=}@1L<1MqQzX%K+0j3t|g2H^>ujG5pJ$*<Xvjz$~h3kK~McuCQ~dLM!Cu?Er%5=VApcGw@eBFlbv|E49X}LbD3Hsrc$YV9w=avREB}0M&az>3#uB$Y?clnbtVr=DO4t+xQ3XO!IB_MnWL)<g?ulA$+P*C=uL<qh+;OIhutv?VNe#lsSGmnGl&jE5d#H<ie!xx4Tq^b6$qIWU9I>j9dgK}DiH-y5Ii4<R8pnvGF3=nf~?2|cN;Dpo;eN$9^@BDjb<?f{tP@dcs~kc62JmI2)-d}2v1$vpfw^8IaDD&pFC(hJ2JsoF+m729Ev9R(JlL-Oh_$&cm$vb9PEH5%v#7K&>Twv6?sIaj=>KAC*b~2g<RBNKY_3m-=I%xUVtYPHst`|!`-HX+C^a<gs@aXO+)5GGzhLJd<~-Md~bnTuC-eJ3z;w+MF"
_decoded = zlib.decompress(base64.b85decode(_PACKED))
if len(_decoded) != 3 * _PALETTE_COLOURS + WIDTH * HEIGHT:
    raise ValueError("invalid GTA VI background data")
_palette = _decoded[:3 * _PALETTE_COLOURS]
_indices = _decoded[3 * _PALETTE_COLOURS:]
RGB = bytes(channel for i in _indices for channel in _palette[i*3:i*3+3])
assert len(RGB) == WIDTH * HEIGHT * 3
