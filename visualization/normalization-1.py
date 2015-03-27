import numpy as np
import matplotlib.pyplot as plt

from astropy.visualization import SqrtStretch
from astropy.visualization.mpl_normalize import ImageNormalize

# Generate test image
image = np.arange(65536).reshape((256, 256))

# Create normalizer object
norm = ImageNormalize(vmin=0., vmax=65536, stretch=SqrtStretch())

# Make the figure
fig = plt.figure()
ax = fig.add_subplot(1,1,1)
im = ax.imshow(image, origin='lower', norm=norm)
fig.colorbar(im)