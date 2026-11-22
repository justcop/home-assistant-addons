"""Compact 25×19 RGBA rendition of the user's sunset-and-palms GTA VI art.

Built at the native Govee pixel density. The purple/gold VI letter outlines
were reinforced at one-pixel width so they remain distinct on the 52×32 matrix.
No Pillow or downloaded assets are required at runtime.
"""
import base64
import zlib

WIDTH, HEIGHT = 25, 19
_PACKED = "c-oDXZ%7ki9LG23w%fVd!YTd5*+|pShzeX}A?ro|h>^s4QxqCbp&~@08YBeK%k0k>RxDo(+N`K1np@>RBEzAHFS5cG27&}J8#Q;XzTF*M-Lbpd9X#A~&-42|pWo-XhdYP51rC{};$4m$v2tsme<Q@27(*&fZjQ?>t^Wnpni;4}V^9^w1i5?%X1t(s7Av+az<!KkpZR=4*=JN92DIggtU8(J+KL3~%06K0%9v;!+*p-m1orZIc$>pn^EUE1_EyiLlz5xPVEPT#_c&5KiRO|xnpRB<T(~t_Xx9sLjW>7xci=Pj>7sBNU*nMBo#=iuV@;};aOd^m95Ezt;rgCsqi}175v&@_s*#yLql=)87)HBcO7fk)F;An{7(+lmh})Vz0mH9*fgmx0$ntT41D|n^h~he(=goU1%HUkS8b>ij4q!|l5uA~s{AU=a_u|Le3CS}zpzT3e^8`^%k0gT``=WV(33UJ;i4fxCBQ`c^?!~<MDdvcWSq^;0r-H|r)OKTz3`;UNmt<i8R7p3!lU;1=H-jIU(hyJ)lB|u1Td)K%qxK`Nxi8CL<7+#S+TcZM^(`zGb>L_54Pb{))}LjNJ|s<@NRU2R2IoR;zl0^b2We{?s7)8~$9Y+FpZJW$qIOW$E1))C&B<VMP8|p3I)QYJ3pn7)?M~09t54FkH6p#=os+@2WLh1d>T7`0yCfY1jJn-ea_#}ucu*08&FioMo^{H+nf*O#L#BbgpWmj4@$Z}5Ws-Fec(htTowXo!ZM|X!m#fH`spoD!;8o-I9lc@(=c2ThT>l`sd?hhb?Jv_?!9V"
RGBA = zlib.decompress(base64.b85decode(_PACKED))
if len(RGBA) != WIDTH * HEIGHT * 4:
    raise ValueError("corrupt GTA VI pixel art")
