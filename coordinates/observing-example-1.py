import numpy as np
import matplotlib.pyplot as plt
from astropy import units as u
from astropy.time import Time
from astropy.coordinates import SkyCoord, EarthLocation, AltAz

#supress the warning about vector transforms so as not to clutter the doc build log
import warnings
warnings.filterwarnings('ignore',module='astropy.coordinates.baseframe')

m33 = SkyCoord.from_name('M33')
bear_mountain = EarthLocation(lat=41.3*u.deg, lon=-74*u.deg, height=390*u.m)
utcoffset = -4*u.hour  # Eastern Daylight Time
midnight = Time('2012-7-13 00:00:00') - utcoffset
delta_midnight = np.linspace(-2, 7, 100)*u.hour
m33altazs = m33.transform_to(AltAz(obstime=midnight+delta_midnight, location=bear_mountain))

plt.plot(delta_midnight, m33altazs.secz)
plt.xlim(-2, 7)
plt.ylim(1, 4)
plt.xlabel('Hours from EDT Midnight')
plt.ylabel('Airmass [Sec(z)]')